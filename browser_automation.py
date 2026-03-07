from __future__ import annotations

import json
import os
import platform
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

CDP_CONNECT_TIMEOUT = 20  # seconds to wait for CDP port to become available
CDP_CONNECT_POLL = 0.5    # poll interval in seconds

# Disciplines to exclude from the discipline filter — these overlap with
# dedicated referto subtypes (Laboratorio, Radiologia, Anatomia Patologica, PS).
_EXCLUDED_DISCIPLINES = {
    "PRESTAZIONI DI LABORATORIO ANALISI CHIMICHE",
    "RADIOLOGIA",
    "PRONTO SOCCORSO",
    "ANATOMIA PATOLOGICA",
}


def _is_discipline_excluded(disciplina: str) -> bool:
    """Return True if the discipline should be excluded from the filter list."""
    upper = disciplina.strip().upper()
    return any(excl in upper for excl in _EXCLUDED_DISCIPLINES)


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

    Returns a dict with keys: progid, channel, process_name, exe_path, cdp_compatible
    or None if detection fails.

    cdp_compatible is False for Legacy Edge (EdgeHTML) whose ProgId starts with 'AppX'.
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

    # Try known ProgId mapping first — all known browsers are CDP-compatible
    for known_progid, (channel, process_name) in PROGID_TO_BROWSER.items():
        if progid.startswith(known_progid.split("-")[0]) or progid == known_progid:
            exe_path = _resolve_progid_exe_path(progid)
            return {
                "progid": progid,
                "channel": channel,
                "process_name": process_name,
                "exe_path": exe_path,
                "cdp_compatible": True,
            }

    # ProgId starting with 'AppX' → Legacy Edge (EdgeHTML), not CDP-compatible
    if progid.startswith("AppX"):
        return {
            "progid": progid,
            "channel": None,
            "process_name": "MicrosoftEdge.exe",
            "exe_path": None,
            "cdp_compatible": False,
        }

    # Unknown ProgId — try to resolve the exe anyway (assume Chromium-based)
    exe_path = _resolve_progid_exe_path(progid)
    if exe_path:
        process_name = Path(exe_path).name.lower()
        return {
            "progid": progid,
            "channel": None,
            "process_name": process_name,
            "exe_path": exe_path,
            "cdp_compatible": True,
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


def _find_any_chromium_browser() -> dict | None:
    """Find any installed Chromium-based browser as a CDP fallback.

    Checks App Paths registry and common filesystem locations for msedge.exe
    and chrome.exe (in that order, preferring Edge since it's pre-installed on
    most Windows 10/11 systems).

    Returns a dict with keys: channel, process_name, exe_path — or None.
    """
    candidates = [
        ("msedge", "msedge.exe"),
        ("chrome", "chrome.exe"),
    ]

    # 1. Check App Paths registry (most reliable)
    for channel, exe_name in candidates:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                    hive,
                    rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
                ) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    if val and Path(val).exists():
                        return {
                            "channel": channel,
                            "process_name": exe_name,
                            "exe_path": str(Path(val)),
                        }
            except OSError:
                pass

    # 2. Check common installation paths
    common_paths = [
        (Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
         / "Microsoft" / "Edge" / "Application" / "msedge.exe",
         "msedge", "msedge.exe"),
        (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
         / "Microsoft" / "Edge" / "Application" / "msedge.exe",
         "msedge", "msedge.exe"),
        (Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
         / "Google" / "Chrome" / "Application" / "chrome.exe",
         "chrome", "chrome.exe"),
        (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
         / "Google" / "Chrome" / "Application" / "chrome.exe",
         "chrome", "chrome.exe"),
    ]
    for path, channel, exe_name in common_paths:
        if path.exists():
            return {
                "channel": channel,
                "process_name": exe_name,
                "exe_path": str(path),
            }

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
    """Read the original shell\\open\\command, preferring HKLM over HKCR.

    HKCR is a merged view of HKLM + HKCU.  After enable_cdp_in_registry()
    writes the HKCU override, HKCR returns the *modified* value — not the
    original system command.  Reading HKLM first avoids this.
    HKCR is kept as fallback for per-user browser installs that only register there.
    """
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CLASSES_ROOT):
        try:
            subkey = (rf"SOFTWARE\Classes\{progid}\shell\open\command"
                      if hive == winreg.HKEY_LOCAL_MACHINE
                      else rf"{progid}\shell\open\command")
            with winreg.OpenKey(hive, subkey) as key:
                cmd, _ = winreg.QueryValueEx(key, "")
                if isinstance(cmd, str) and cmd.strip():
                    return cmd
        except OSError:
            continue
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

    # Verify write
    _verify_cdp_registry_write(progid, port, new_cmd)


def _verify_cdp_registry_write(progid: str, port: int, expected_cmd: str) -> None:
    """Verify that the CDP flag was actually written to the registry.

    Raises RuntimeError if the read-back does not contain the flag.
    """
    cdp_flag = f"--remote-debugging-port={port}"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"SOFTWARE\Classes\{progid}\shell\open\command",
        ) as key:
            actual, _ = winreg.QueryValueEx(key, "")
            if not isinstance(actual, str) or cdp_flag not in actual:
                raise RuntimeError(
                    f"Verifica registro fallita: il flag CDP non risulta scritto.\n"
                    f"Atteso: {expected_cmd}\n"
                    f"Trovato: {actual}"
                )
    except OSError as e:
        raise RuntimeError(
            f"Verifica registro fallita: impossibile rileggere la chiave HKCU.\n"
            f"Errore: {e}"
        )


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


def _has_problematic_cdp_targets(port: int, logger=None) -> bool:
    """Lightweight HTTP check for CDP targets that cause Playwright to hang.

    Queries /json endpoint (no WebSocket, no side effects) to detect "other"
    type targets (Edge newtab, Copilot sidebar) and blank pages that cause
    connect_over_cdp to deadlock.  Returns True if cleanup is needed.
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
        with urllib.request.urlopen(req, timeout=2) as resp:
            targets = json.loads(resp.read())
    except Exception:
        return False

    for t in targets:
        ttype = t.get("type", "")
        url = t.get("url", "")
        if ttype == "other" or (ttype == "page" and not url):
            if logger:
                logger.debug(
                    f"[CDP] Target problematico rilevato: type={ttype} url={url!r}"
                )
            return True
    return False


def _cleanup_cdp_targets(port: int, logger=None) -> int:
    """Close CDP targets that cause Playwright connect_over_cdp to hang.

    Edge's "other" type targets (edge://newtab, Copilot sidebar) and blank pages
    cause Playwright's Target.detachFromTarget commands to go unanswered, producing
    an infinite deadlock.  Pre-closing these targets fixes the issue.

    Returns the number of targets closed.
    """
    import base64 as _b64
    import select as _sel

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
        with urllib.request.urlopen(req, timeout=3) as resp:
            targets = json.loads(resp.read())
    except Exception:
        return 0

    # Identify targets to close:
    #   - type "other" (Edge internal pages: newtab, Copilot sidebar, etc.)
    #   - type "page" with empty URL (prerendered/blank placeholder tabs)
    ids_to_close = []
    for t in targets:
        ttype = t.get("type", "")
        url = t.get("url", "")
        if ttype == "other":
            ids_to_close.append((t["id"], url or t.get("title", "?")))
        elif ttype == "page" and not url:
            ids_to_close.append((t["id"], "(blank page)"))

    if not ids_to_close:
        return 0

    # Get browser-level WebSocket URL
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
        with urllib.request.urlopen(req, timeout=2) as resp:
            info = json.loads(resp.read())
        ws_path = info["webSocketDebuggerUrl"].replace(f"ws://127.0.0.1:{port}", "")
    except Exception:
        return 0

    # Raw WebSocket connection to send Target.closeTarget commands
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        ws_key = _b64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode())
        resp_buf = b""
        while b"\r\n\r\n" not in resp_buf:
            resp_buf += sock.recv(4096)
        if b"101" not in resp_buf:
            sock.close()
            return 0

        closed = 0
        for i, (tid, desc) in enumerate(ids_to_close):
            payload = json.dumps({
                "id": 9000 + i,
                "method": "Target.closeTarget",
                "params": {"targetId": tid},
            }).encode()
            mask_key = os.urandom(4)
            frame = bytearray([0x81])
            plen = len(payload)
            frame.append(0x80 | (plen if plen < 126 else 126))
            if plen >= 126:
                frame.extend(plen.to_bytes(2, "big"))
            frame.extend(mask_key)
            frame.extend(bytearray(b ^ mask_key[j % 4] for j, b in enumerate(payload)))
            sock.sendall(frame)

            # Read response (best effort)
            ready = _sel.select([sock], [], [], 2)
            if ready[0]:
                sock.recv(4096)
                closed += 1
                if logger:
                    logger.debug(f"[CDP cleanup] Chiuso target: {desc}")

        # WebSocket close frame (proper handshake: send close, read ack)
        sock.sendall(bytearray([0x88, 0x80]) + os.urandom(4))
        try:
            sock.recv(1024)  # Read server's close acknowledgment
        except Exception:
            pass
        sock.close()

        if closed and logger:
            logger.info(
                f"Pre-connessione: chiusi {closed} target CDP problematici "
                f"(edge:// e pagine vuote)"
            )
        return closed

    except Exception as e:
        if logger:
            logger.debug(f"[CDP cleanup] Errore: {e}")
        return 0


def _is_cdp_port_available(port: int) -> bool:
    """Check if a CDP endpoint is responding on 127.0.0.1:port via HTTP /json/version."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        # Fallback to raw socket check (e.g. if /json/version is slow)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            return False


def _is_browser_process_running(process_name: str, logger=None) -> bool:
    """Check if a browser process is currently running via tasklist."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        running = process_name.lower() in result.stdout.lower()
        if logger:
            logger.debug(f"_is_browser_process_running({process_name}): {running}")
        return running
    except Exception as e:
        if logger:
            logger.debug(f"_is_browser_process_running({process_name}): errore {e}")
        return False


def _browser_has_visible_windows(process_name: str, logger=None) -> bool:
    """Check if a browser process has visible windows (not just background processes).

    Uses 'tasklist /V' to inspect window titles. Background-only processes
    show 'N/D' or 'N/A' as window title (locale-dependent).
    Returns True if at least one process has a real window title.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/V", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in result.stdout.strip().splitlines():
            # CSV format: "Image Name","PID","Session Name","Session#","Mem Usage","Status","User Name","CPU Time","Window Title"
            parts = line.split('","')
            if len(parts) >= 9:
                title = parts[-1].strip('"').strip()
                # Background processes have "N/D" (Italian), "N/A" (English), or empty title.
                # "OleMainThreadWndName" is an internal OLE system window (not visible).
                if title and title not in ("N/D", "N/A", "Non disponibile", "OleMainThreadWndName"):
                    if logger:
                        logger.debug(
                            f"_browser_has_visible_windows({process_name}): True "
                            f"(titolo: {title[:60]})"
                        )
                    return True
        if logger:
            logger.debug(f"_browser_has_visible_windows({process_name}): False (solo processi in background)")
        return False
    except Exception as e:
        if logger:
            logger.debug(f"_browser_has_visible_windows({process_name}): errore {e}")
        return True  # Assume visible on error (safe fallback: don't kill anything)


def _find_powershell() -> str:
    """Find powershell.exe, trying absolute paths if it's not in PATH."""
    # Try PATH first
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "echo ok"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return "powershell"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try well-known absolute paths
    for candidate in (
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe",
    ):
        if Path(candidate).exists():
            return candidate

    return ""  # empty string → not found


def _browser_has_cdp_flag(process_name: str, port: int, logger=None) -> bool | None:
    """Check if the running browser process was launched with --remote-debugging-port.

    Returns True/False, or None if the check failed.
    Tries WMIC first (fast), falls back to PowerShell Get-CimInstance if WMIC
    is unavailable (deprecated on Win10 21H1+, removed on some Win11 builds).
    """
    flag = f"--remote-debugging-port={port}"

    # Try WMIC first (fast when available)
    try:
        result = subprocess.run(
            ["wmic", "process", "where", f"name='{process_name}'",
             "get", "CommandLine", "/FORMAT:LIST"],
            capture_output=True, text=True, timeout=5,
        )
        found = flag in result.stdout
        if logger:
            logger.debug(
                f"_browser_has_cdp_flag({process_name}, {port}) [wmic]: {found}"
            )
        return found
    except FileNotFoundError:
        if logger:
            logger.debug("_browser_has_cdp_flag: wmic non disponibile, provo PowerShell")
    except Exception as e:
        if logger:
            logger.debug(f"_browser_has_cdp_flag: errore wmic ({e}), provo PowerShell")

    # Fallback to PowerShell Get-CimInstance
    ps_exe = _find_powershell()
    if not ps_exe:
        if logger:
            logger.debug("_browser_has_cdp_flag: PowerShell non trovato")
        return None

    try:
        ps_cmd = (
            f"Get-CimInstance Win32_Process -Filter \"name='{process_name}'\" "
            f"| Select-Object -ExpandProperty CommandLine"
        )
        result = subprocess.run(
            [ps_exe, "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        found = flag in result.stdout
        if logger:
            logger.debug(
                f"_browser_has_cdp_flag({process_name}, {port}) [powershell]: {found}"
            )
            if result.stderr.strip():
                logger.debug(f"_browser_has_cdp_flag powershell stderr: {result.stderr.strip()[:200]}")
        return found
    except Exception as e:
        if logger:
            logger.debug(f"_browser_has_cdp_flag: errore PowerShell ({e})")
        return None


class BrowserCDPNotActive(Exception):
    """Raised when the browser is running but CDP is not active.

    The GUI should catch this and ask the user if they want to restart
    the browser with CDP enabled.
    """
    def __init__(self, message: str, process_name: str, exe_path: str, port: int):
        super().__init__(message)
        self.process_name = process_name
        self.exe_path = exe_path
        self.port = port


def _kill_browser_processes(process_name: str, timeout: int = 10, logger=None) -> None:
    """Kill all instances of a browser process and wait until they're gone."""
    if logger:
        logger.debug(f"_kill_browser_processes({process_name}) avviato")
    t0 = time.time()
    for _ in range(3):
        subprocess.run(
            ["taskkill", "/IM", process_name, "/F", "/T"],
            capture_output=True, timeout=5,
        )
        time.sleep(1)

    # Wait for processes to actually terminate
    elapsed = 0
    while elapsed < timeout:
        if not _is_browser_process_running(process_name, logger=logger):
            if logger:
                logger.debug(f"_kill_browser_processes completato in {time.time()-t0:.1f}s")
            return
        time.sleep(1)
        elapsed += 1
    if logger:
        logger.debug(f"_kill_browser_processes: timeout dopo {time.time()-t0:.1f}s")


def _launch_browser_with_cdp(exe_path: str, port: int, restore_session: bool = False, logger=None) -> None:
    """Launch a browser with --remote-debugging-port as a detached process."""
    if logger:
        logger.debug(f"_launch_browser_with_cdp({exe_path}, port={port}, restore_session={restore_session})")
    args = [exe_path, f"--remote-debugging-port={port}"]
    if restore_session:
        args.append("--restore-last-session")
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    if logger:
        logger.debug("Browser lanciato, in attesa della porta CDP...")


@dataclass
class DocumentResult:
    disciplina: str
    skipped: bool
    download_path: Path | None
    error: str | None
    date_text: str = ""


@dataclass
class PatientDocumentInfo:
    row_index: int
    date_text: str
    tipo_text: str
    ente_text: str
    disciplina_text: str = ""


def _read_hkcu_open_command(progid: str) -> str | None:
    """Read shell\\open\\command specifically from HKCU (user override)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"SOFTWARE\Classes\{progid}\shell\open\command",
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, "")
            return cmd if isinstance(cmd, str) else None
    except OSError:
        return None


def _read_hklm_open_command(progid: str) -> str | None:
    """Read shell\\open\\command specifically from HKLM (machine-level install)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SOFTWARE\Classes\{progid}\shell\open\command",
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, "")
            return cmd if isinstance(cmd, str) else None
    except OSError:
        return None


def _get_browser_version(exe_path: str) -> str:
    """Get the browser version from the exe via PowerShell Get-Item."""
    if not exe_path or not Path(exe_path).exists():
        return "(exe non trovato)"
    ps_exe = _find_powershell()
    if not ps_exe:
        return "(PowerShell non disponibile)"
    try:
        result = subprocess.run(
            [ps_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", f"(Get-Item '{exe_path}').VersionInfo.ProductVersion"],
            capture_output=True, text=True, timeout=10,
        )
        ver = result.stdout.strip()
        return ver if ver else "(versione non disponibile)"
    except Exception as e:
        return f"(errore: {e})"


def _test_playwright_connect(port: int) -> tuple[bool, str]:
    """Test Playwright CDP connection and immediately close.

    Returns (success: bool, detail: str).
    """
    try:
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}", timeout=10000
            )
            contexts = len(browser.contexts)
            pages = sum(len(c.pages) for c in browser.contexts)
            browser.close()
            return True, f"{contexts} contesti, {pages} pagine"
        except Exception as e:
            return False, str(e)
        finally:
            pw.stop()
    except Exception as e:
        return False, f"Errore avvio Playwright: {e}"


def run_cdp_diagnostics(port: int) -> str:
    """Run comprehensive CDP diagnostics and return a copiable text report.

    Sections:
    1. Sistema (OS, arch, date)
    2. Browser (ProgId, exe, version, CDP compatibility)
    3. Registro (HKCU override, HKLM original, CDP flag)
    4. Processo (running? CDP flag in command line?)
    5. Porta CDP (responding? /json/version? targets)
    6. Connessione Playwright (quick connect+close test)
    7. Sessione SISS (probe via CDP)
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  DIAGNOSTICA CDP — FSE Processor")
    lines.append("=" * 60)

    # ── 1. Sistema ──
    lines.append("")
    lines.append("── 1. SISTEMA ──")
    lines.append(f"  Data/ora:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  Windows:       {platform.platform()}")
    lines.append(f"  Architettura:  {platform.machine()}")
    lines.append(f"  Python:        {platform.python_version()}")
    ps_exe = _find_powershell()
    lines.append(f"  PowerShell:    {'[OK] ' + ps_exe if ps_exe else '[PROBLEMA] Non trovato'}")

    # ── 2. Browser ──
    lines.append("")
    lines.append("── 2. BROWSER PREDEFINITO ──")
    browser_info = detect_default_browser()
    if browser_info:
        progid = browser_info["progid"]
        exe_path = browser_info.get("exe_path")
        cdp_compat = browser_info.get("cdp_compatible", True)
        lines.append(f"  ProgId:        {progid}")
        lines.append(f"  Exe:           {exe_path or '(non trovato)'}")
        lines.append(f"  CDP compat.:   {'[OK] Si' if cdp_compat else '[PROBLEMA] No (Legacy Edge?)'}")
        if exe_path:
            version = _get_browser_version(exe_path)
            lines.append(f"  Versione:      {version}")
            exe_exists = Path(exe_path).exists()
            lines.append(f"  Exe esiste:    {'[OK] Si' if exe_exists else '[PROBLEMA] No'}")
        else:
            lines.append(f"  Versione:      (exe non disponibile)")
    else:
        progid = None
        exe_path = None
        lines.append("  [PROBLEMA] Browser predefinito non rilevato")

    # ── 3. Registro ──
    lines.append("")
    lines.append("── 3. REGISTRO WINDOWS ──")
    if progid:
        hklm_cmd = _read_hklm_open_command(progid)
        hkcu_cmd = _read_hkcu_open_command(progid)
        cdp_flag = f"--remote-debugging-port={port}"

        lines.append(f"  HKLM original: {hklm_cmd or '(non presente)'}")
        lines.append(f"  HKCU override: {hkcu_cmd or '(non presente)'}")

        if hkcu_cmd:
            has_cdp_in_hkcu = cdp_flag in hkcu_cmd
            lines.append(f"  Flag CDP HKCU: {'[OK] Presente' if has_cdp_in_hkcu else '[PROBLEMA] Assente'}")
        else:
            lines.append(f"  Flag CDP HKCU: [INFO] Nessun override HKCU")

        if hklm_cmd and cdp_flag in hklm_cmd:
            lines.append(f"  [INFO] Flag CDP presente anche in HKLM (insolito)")

        # Check if HKCR returns the HKCU value (merged view)
        try:
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                rf"{progid}\shell\open\command",
            ) as key:
                hkcr_cmd, _ = winreg.QueryValueEx(key, "")
                if isinstance(hkcr_cmd, str):
                    lines.append(f"  HKCR (merged): {hkcr_cmd}")
        except OSError:
            lines.append(f"  HKCR (merged): (non leggibile)")
    else:
        lines.append("  (nessun ProgId da verificare)")

    # ── 4. Processo ──
    lines.append("")
    lines.append("── 4. PROCESSO BROWSER ──")
    process_name = browser_info["process_name"] if browser_info else None
    if process_name:
        running = _is_browser_process_running(process_name)
        lines.append(f"  {process_name}: {'[OK] In esecuzione' if running else '[INFO] Non in esecuzione'}")
        if running:
            has_flag = _browser_has_cdp_flag(process_name, port)
            if has_flag is True:
                lines.append(f"  Flag CDP processo: [OK] Presente")
            elif has_flag is False:
                lines.append(f"  Flag CDP processo: [PROBLEMA] Assente — browser avviato senza CDP")
            else:
                lines.append(f"  Flag CDP processo: [PROBLEMA] Verifica fallita (WMIC/PS non disponibili)")

            # Log raw command lines
            try:
                result = subprocess.run(
                    ["wmic", "process", "where", f"name='{process_name}'",
                     "get", "CommandLine", "/FORMAT:LIST"],
                    capture_output=True, text=True, timeout=5,
                )
                cmdlines = [l.strip() for l in result.stdout.splitlines()
                            if l.strip() and l.strip().startswith("CommandLine=")]
                for cl in cmdlines[:5]:
                    lines.append(f"    {cl[:200]}")
            except Exception:
                lines.append("    (impossibile leggere command line)")
    else:
        lines.append("  (nessun processo da verificare)")

    # ── 5. Porta CDP ──
    lines.append("")
    lines.append(f"── 5. PORTA CDP ({port}) ──")
    port_active = _is_cdp_port_available(port)
    lines.append(f"  Porta attiva:  {'[OK] Si' if port_active else '[INFO] No'}")

    if port_active:
        # /json/version
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
            with urllib.request.urlopen(req, timeout=3) as resp:
                version_info = json.loads(resp.read())
            lines.append(f"  Browser:       {version_info.get('Browser', '?')}")
            lines.append(f"  Protocol:      {version_info.get('Protocol-Version', '?')}")
            lines.append(f"  User-Agent:    {version_info.get('User-Agent', '?')[:80]}")
            lines.append(f"  WebSocket URL: {version_info.get('webSocketDebuggerUrl', '?')}")
        except Exception as e:
            lines.append(f"  /json/version: [PROBLEMA] {e}")

        # /json targets list
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
            with urllib.request.urlopen(req, timeout=3) as resp:
                targets = json.loads(resp.read())
            lines.append(f"  Target totali: {len(targets)}")
            for i, t in enumerate(targets[:10]):
                ttype = t.get("type", "?")
                url = t.get("url", "")[:80]
                title = t.get("title", "")[:40]
                lines.append(f"    [{i}] {ttype}: {title} — {url}")
            if len(targets) > 10:
                lines.append(f"    ... e altri {len(targets) - 10} target")
        except Exception as e:
            lines.append(f"  /json (targets): [PROBLEMA] {e}")
    else:
        lines.append("  (porta non attiva — sezioni 6 e 7 potrebbero fallire)")

    # ── 6. Connessione Playwright ──
    lines.append("")
    lines.append("── 6. CONNESSIONE PLAYWRIGHT ──")
    if port_active:
        pw_ok, pw_detail = _test_playwright_connect(port)
        if pw_ok:
            lines.append(f"  [OK] Connessione riuscita: {pw_detail}")
        else:
            lines.append(f"  [PROBLEMA] Connessione fallita: {pw_detail}")
    else:
        lines.append("  [INFO] Saltata (porta CDP non attiva)")

    # ── 7. Sessione SISS ──
    lines.append("")
    lines.append("── 7. SESSIONE SISS ──")
    if port_active:
        siss_result = _check_siss_via_cdp(port)
        if siss_result is True:
            lines.append("  [OK] Sessione SISS attiva")
        elif siss_result is False:
            lines.append("  [INFO] Nessuna sessione SISS (redirect a SSO login)")
        elif siss_result == _SISS_UNREACHABLE:
            lines.append("  [PROBLEMA] Portale SISS non raggiungibile")
        else:
            lines.append("  [PROBLEMA] CDP non disponibile per il probe")
    else:
        lines.append("  [INFO] Saltata (porta CDP non attiva)")

    lines.append("")
    lines.append("=" * 60)
    lines.append("  Fine diagnostica")
    lines.append("=" * 60)

    return "\n".join(lines)


_SISS_UNREACHABLE = "unreachable"  # sentinel for portal unreachable


def _create_background_tab(port: int, url: str, ws_url: str) -> str | None:
    """Create a browser tab in the background via CDP WebSocket.

    Uses Target.createTarget with background:true to avoid bringing the
    browser window to the foreground.  Returns the targetId or None on failure.
    """
    import base64 as _b64
    import select as _sel

    if not ws_url:
        return None

    ws_path = ws_url.replace(f"ws://127.0.0.1:{port}", "")
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        ws_key = _b64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode())
        resp_buf = b""
        while b"\r\n\r\n" not in resp_buf:
            resp_buf += sock.recv(4096)
        if b"101" not in resp_buf:
            sock.close()
            return None

        # Send Target.createTarget with background: true
        payload = json.dumps({
            "id": 1,
            "method": "Target.createTarget",
            "params": {"url": url, "background": True},
        }).encode()
        mask_key = os.urandom(4)
        frame = bytearray([0x81])
        plen = len(payload)
        frame.append(0x80 | (plen if plen < 126 else 126))
        if plen >= 126:
            frame.extend(plen.to_bytes(2, "big"))
        frame.extend(mask_key)
        frame.extend(bytearray(b ^ mask_key[j % 4] for j, b in enumerate(payload)))
        sock.sendall(frame)

        # Read response to get targetId
        target_id = None
        ready = _sel.select([sock], [], [], 5)
        if ready[0]:
            data = sock.recv(8192)
            # Skip WebSocket frame header to get JSON payload
            if len(data) > 2:
                offset = 2
                plen_byte = data[1] & 0x7F
                if plen_byte == 126:
                    offset = 4
                elif plen_byte == 127:
                    offset = 10
                try:
                    resp = json.loads(data[offset:])
                    target_id = resp.get("result", {}).get("targetId")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

        # Close WebSocket cleanly
        sock.sendall(bytearray([0x88, 0x80]) + os.urandom(4))
        try:
            sock.recv(1024)
        except Exception:
            pass
        sock.close()
        return target_id
    except Exception:
        return None


def _create_tab_via_http(base: str, url: str) -> str | None:
    """Fallback: create a tab via HTTP /json/new (may bring browser to foreground)."""
    try:
        req = urllib.request.Request(f"{base}/json/new?{url}", method="PUT")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tab = json.loads(resp.read())
        return tab.get("id")
    except urllib.error.HTTPError:
        try:
            req = urllib.request.Request(f"{base}/json/new?{url}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                tab = json.loads(resp.read())
            return tab.get("id")
        except Exception:
            return None
    except Exception:
        return None


def _check_siss_via_cdp(port: int) -> bool | str | None:
    """Check SISS session status via CDP.

    Strategy:
      1. Quick scan of existing browser tabs for an active SISS page
         (avoids creating probe tabs entirely in the common case).
      2. If no SISS tab exists, create a temporary probe tab to test
         whether the session cookie is still valid.

    Returns:
        True   – session is active
        False  – no active session (redirected to SSO login)
        _SISS_UNREACHABLE – SISS portal is unreachable (network error page)
        None   – CDP not available
    """
    base = f"http://127.0.0.1:{port}"
    fse_url = "https://operatorisiss.servizirl.it/opefseie/"

    # Quick check: is CDP available at all?
    try:
        req = urllib.request.Request(f"{base}/json/version")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status != 200:
                return None
    except Exception:
        return None

    # ── Step 1: scan existing tabs for an active SISS page ──
    tabs = []
    try:
        req = urllib.request.Request(f"{base}/json/list")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tabs = json.loads(resp.read())
        for tab in tabs:
            url = tab.get("url", "").lower()
            title = tab.get("title", "").lower()
            # Tab is on SISS portal (not on SSO login) → session active
            if (("operatorisiss" in url or "servizirl" in url)
                    and "idpcrlmain" not in url
                    and "ssoauth" not in url):
                return True
            # Check for unreachable error on an existing SISS tab
            if "impossibile raggiungere" in title and "siss" in title:
                return _SISS_UNREACHABLE
    except Exception:
        pass

    # ── Step 2: no SISS tab found — create a probe tab ──
    # Remember the currently active tab to re-activate it later
    active_tab_id = tabs[0].get("id") if tabs else None

    # Open a temporary probe tab (PUT for newer Edge, GET fallback)
    probe_id = None
    try:
        req = urllib.request.Request(f"{base}/json/new?{fse_url}", method="PUT")
        with urllib.request.urlopen(req, timeout=5) as resp:
            probe_tab = json.loads(resp.read())
        probe_id = probe_tab.get("id")
    except urllib.error.HTTPError:
        try:
            req = urllib.request.Request(f"{base}/json/new?{fse_url}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                probe_tab = json.loads(resp.read())
            probe_id = probe_tab.get("id")
        except Exception:
            pass
    except Exception:
        pass

    if not probe_id:
        return None

    try:
        # Re-activate the original tab so the probe stays in background
        if active_tab_id:
            try:
                req = urllib.request.Request(
                    f"{base}/json/activate/{active_tab_id}"
                )
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass

        # Poll the probe tab URL until it resolves (up to 8s, check every 0.5s)
        final_url = ""
        final_title = ""
        for poll_i in range(16):  # 16 × 0.5s = 8s max
            time.sleep(0.5)
            try:
                req = urllib.request.Request(f"{base}/json/list")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    tabs_now = json.loads(resp.read())
                for tab in tabs_now:
                    if tab.get("id") == probe_id:
                        url = tab.get("url", "").lower()
                        title = tab.get("title", "")
                        # Still loading from blank
                        if not url or url in ("", "about:blank"):
                            break
                        # URL is the FSE portal — if we've waited >= 3s
                        # and no redirect happened, the session IS active.
                        # (unauthenticated users get redirected to SSO
                        # within 1-2s; staying on fse_url means logged in)
                        if url == fse_url.lower():
                            if poll_i >= 6:
                                final_url = url
                                final_title = title
                            break
                        # URL changed to something else (SSO login, etc.)
                        final_url = url
                        final_title = title
                        break
            except Exception:
                continue
            if final_url:
                break

        # Check for browser error page (network unreachable)
        if "impossibile raggiungere" in final_title.lower():
            return _SISS_UNREACHABLE

        # Redirected to SSO login → no active session
        if "idpcrlmain" in final_url or "ssoauth" in final_url:
            return False
        # Stayed on SISS portal → session is active
        if "operatorisiss" in final_url or "servizirl" in final_url:
            return True
        return False
    finally:
        # Always close the probe tab
        try:
            req = urllib.request.Request(f"{base}/json/close/{probe_id}")
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass


class FSEBrowser:
    def __init__(self, config: Config, logger: ProcessingLogger) -> None:
        self._config = config
        self._logger = logger
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None  # Only used in CDP mode
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._owned_page: Page | None = None  # The tab WE created/found — safe to close
        self._attached = False  # True when connected to existing browser via CDP
        self._otp_callback: callable | None = None  # GUI callback to request OTP from user

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
                    self._logger.info(
                        f"Modalità DCP disabilitata nelle impostazioni"
                        f"{channel} non verrà usato per l'automazione. "
                        f"Verra' avviato Chromium integrato: sara' necessario un nuovo login SSO."
                        f"\nAbilita l'opzione 'Usa browser CDP' nelle impostazioni per utilizzare il browser già in sessione"
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
        self._owned_page = self._page
        self._page.set_default_timeout(self._config.page_timeout)
        self._logger.info(f"Browser avviato (channel={channel}, headless={self._config.headless})")

    def _log_system_info(self, browser_info: dict | None, port: int) -> None:
        """Log diagnostic system info at the start of CDP connection."""
        win_ver = platform.version()
        self._logger.debug(f"[CDP] Windows version: {win_ver}")

        # Log HTTPS ProgId from registry
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
            ) as key:
                progid, _ = winreg.QueryValueEx(key, "ProgId")
                self._logger.debug(f"[CDP] HTTPS ProgId dal registro: {progid}")
        except OSError:
            self._logger.debug("[CDP] HTTPS ProgId: non trovato nel registro")

        if browser_info:
            cdp_compat = browser_info.get("cdp_compatible", True)
            self._logger.debug(
                f"[CDP] Browser predefinito: progid={browser_info['progid']}, "
                f"cdp_compatible={cdp_compat}, exe={browser_info.get('exe_path')}"
            )
        else:
            self._logger.debug("[CDP] Browser predefinito: non rilevato")

    def _start_cdp(self) -> None:
        """Connect to an existing browser via CDP, or launch one if not running.

        IMPORTANT: This method NEVER kills the user's browser.
        If the browser is running (with or without CDP), it will try to connect
        with retries. Only if no browser process is found will it launch one.

        On Windows 10 with Legacy Edge (EdgeHTML) as default browser, this method
        will automatically find an installed Chromium browser (Edge or Chrome)
        as a fallback, since Legacy Edge does not support CDP.
        """
        port = self._config.cdp_port
        endpoint = f"http://127.0.0.1:{port}"
        channel = self._config.browser_channel

        # 0. Quick CDP port scan — if port is already active, try connecting
        #    directly before any browser detection. Covers the case where a
        #    Chromium browser is already running with CDP but Legacy Edge is
        #    the default browser.
        if _is_cdp_port_available(port):
            self._logger.info(
                f"Porta CDP {port} gia' attiva, tentativo connessione diretta..."
            )
            try:
                self._connect_cdp(endpoint, port)
                self._logger.info(f"Connesso direttamente via CDP (porta {port})")
                return
            except ConnectionError as e:
                self._logger.debug(
                    f"[CDP] Connessione diretta fallita, procedo con rilevamento: {e}"
                )

        # 1. Detect browser info + log diagnostics
        browser_info = detect_default_browser()
        self._log_system_info(browser_info, port)
        process_name = None
        exe_path = None

        if browser_info:
            cdp_compatible = browser_info.get("cdp_compatible", True)
            process_name = browser_info["process_name"]
            exe_path = browser_info["exe_path"]

            # Legacy Edge or other non-CDP browser detected
            if not cdp_compatible:
                self._logger.warning(
                    f"Browser predefinito ({browser_info['progid']}) non supporta CDP. "
                    f"Ricerca browser Chromium alternativo..."
                )
                alt = _find_any_chromium_browser()
                if alt:
                    self._logger.info(
                        f"Browser Chromium alternativo trovato: {alt['exe_path']}"
                    )
                    process_name = alt["process_name"]
                    exe_path = alt["exe_path"]
                    channel = alt["channel"] or channel
                else:
                    raise ConnectionError(
                        f"Il browser predefinito ({browser_info['progid']}) non supporta CDP "
                        f"e non e' stato trovato nessun browser Chromium (Edge o Chrome) installato.\n"
                        f"Installa Microsoft Edge (Chromium) o Google Chrome per usare la modalita' CDP."
                    )

        if not exe_path:
            # detect_default_browser() returned None — try finding any Chromium browser
            alt = _find_any_chromium_browser()
            if alt:
                self._logger.info(
                    f"Browser predefinito non rilevato, trovato: {alt['exe_path']}"
                )
                process_name = alt["process_name"]
                exe_path = alt["exe_path"]
                channel = alt["channel"] or channel
            else:
                exe_path = self._resolve_exe_from_channel(channel)
                self._logger.debug(f"[CDP] exe_path da channel '{channel}': {exe_path}")
        if not process_name and channel in ("msedge", "chrome"):
            process_name = "msedge.exe" if channel == "msedge" else "chrome.exe"

        self._logger.debug(f"[CDP] process_name={process_name}, exe_path={exe_path}, port={port}")

        # 1b. Auto-enforce CDP in registry for future launches
        #     Ensures every future browser start (from Millewin, shortcuts, links)
        #     will have CDP active automatically.
        if browser_info and browser_info.get("cdp_compatible", True):
            progid = browser_info["progid"]
            if not get_cdp_registry_status(progid, port):
                try:
                    enable_cdp_in_registry(progid, port)
                    self._logger.info(
                        f"[CDP] Flag CDP attivato automaticamente nel registro per {progid}"
                    )
                except Exception as e:
                    self._logger.debug(f"[CDP] Impossibile attivare CDP nel registro: {e}")

        # 2. Check if browser process is running
        browser_running = (
            _is_browser_process_running(process_name, logger=self._logger)
            if process_name else False
        )
        self._logger.debug(f"[CDP] browser_running={browser_running}")

        # 3. Browser IS running — quick port check to pick optimal strategy.
        #    Avoids 45s of blind retries when only background processes exist.
        if browser_running:
            cdp_port_active = _is_cdp_port_available(port)
            self._logger.debug(f"[CDP] cdp_port_active={cdp_port_active}")

            if cdp_port_active:
                # CDP port responding — try connecting with retries
                last_error = None
                for attempt in range(1, 4):
                    self._logger.info(
                        f"Tentativo connessione CDP {attempt}/3 su porta {port}..."
                    )
                    try:
                        self._connect_cdp(endpoint, port)
                        return  # success
                    except ConnectionError as e:
                        last_error = e
                        self._logger.warning(
                            f"Tentativo {attempt}/3 fallito: {e}"
                        )
                        if attempt < 3:
                            time.sleep(attempt * 2)  # 2s, 4s backoff

                # All retries exhausted — stale debugger session
                if exe_path and Path(exe_path).exists():
                    self._logger.warning(
                        f"Porta CDP {port} attiva ma connessione Playwright fallisce "
                        f"(probabile sessione debugger stale). Necessario riavvio."
                    )
                    raise BrowserCDPNotActive(
                        f"Il browser ha CDP attivo sulla porta {port} ma non risponde.\n"
                        f"Probabilmente una sessione precedente non si e' chiusa correttamente.\n"
                        f"Vuoi riavviare il browser? Le tab aperte verranno chiuse.",
                        process_name=process_name,
                        exe_path=exe_path,
                        port=port,
                    )
                raise ConnectionError(
                    f"CDP porta {port} attiva ma connessione fallita dopo 3 tentativi.\n"
                    f"Ultimo errore: {last_error}\n"
                    f"Prova a chiudere e riaprire manualmente il browser."
                )

            # CDP port NOT responding — check if browser was launched with CDP flag
            has_flag = _browser_has_cdp_flag(process_name, port, logger=self._logger)
            self._logger.debug(f"[CDP] has_flag={has_flag} (porta non attiva)")

            if has_flag:
                # CDP flag present but port dead — browser frozen
                if exe_path and Path(exe_path).exists():
                    self._logger.warning(
                        f"Browser con flag CDP ma porta {port} non risponde. "
                        f"Necessario riavvio."
                    )
                    raise BrowserCDPNotActive(
                        f"Il browser ha il flag CDP sulla porta {port} ma non risponde.\n"
                        f"Vuoi riavviare il browser? Le tab aperte verranno chiuse.",
                        process_name=process_name,
                        exe_path=exe_path,
                        port=port,
                    )
                raise ConnectionError(
                    f"Browser con flag CDP non risponde sulla porta {port}.\n"
                    f"Chiudi e riapri manualmente il browser."
                )

            # has_flag is False or None — no CDP flag (or check failed).
            # The browser was started from a shortcut, Start Menu, or
            # another app (e.g. Millewin) BEFORE the registry override
            # was in place, so it's running without --remote-debugging-port.
            #
            # Check if the browser has actual visible windows or just
            # background processes. Edge/Chrome keep background processes
            # running (notifications, updates, PWAs) even with no windows.
            has_windows = _browser_has_visible_windows(
                process_name, logger=self._logger
            )

            if not has_windows and exe_path and Path(exe_path).exists():
                # Only background processes — safe to kill and relaunch with CDP.
                # Edge's single-instance mechanism would absorb a plain launch,
                # so we must kill background processes first.
                self._logger.info(
                    f"[CDP] Solo processi {process_name} in background "
                    f"(nessuna finestra visibile). Terminazione e rilancio con CDP..."
                )
                _kill_browser_processes(process_name, logger=self._logger)
                time.sleep(1)  # Let processes fully exit
                _launch_browser_with_cdp(exe_path, port, logger=self._logger)

                port_ready = False
                elapsed = 0.0
                while elapsed < CDP_CONNECT_TIMEOUT:
                    if _is_cdp_port_available(port):
                        port_ready = True
                        break
                    time.sleep(CDP_CONNECT_POLL)
                    elapsed += CDP_CONNECT_POLL

                if not port_ready:
                    raise ConnectionError(
                        f"Browser lanciato ma la porta CDP {port} non risponde "
                        f"entro {CDP_CONNECT_TIMEOUT} secondi."
                    )

                self._logger.debug("[CDP] Porta attiva, attesa stabilizzazione browser...")
                time.sleep(3)

                last_error = None
                for attempt in range(1, 3):
                    try:
                        self._connect_cdp(endpoint, port)
                        self._logger.info(
                            f"Connesso al browser lanciato da background "
                            f"(tentativo {attempt})"
                        )
                        return
                    except ConnectionError as e:
                        last_error = e
                        if attempt < 2:
                            time.sleep(2)
                raise ConnectionError(
                    f"Browser lanciato ma connessione CDP fallita: {last_error}"
                )

            # Browser has actual visible windows but no CDP — needs manual restart.
            # IMPORTANT: Do NOT try to launch a new browser window here.
            # Edge's single-instance mechanism absorbs the launch, creating
            # unwanted tabs without enabling CDP.
            if has_flag is None:
                self._logger.warning(
                    f"[CDP] Impossibile verificare i flag del processo {process_name} "
                    f"(WMIC e PowerShell entrambi falliti). "
                    f"Il browser potrebbe avere CDP ma non e' verificabile."
                )
            else:
                self._logger.info(
                    f"[CDP] Il browser {process_name} e' in esecuzione SENZA flag CDP. "
                    f"Necessario riavvio manuale del browser."
                )

            raise BrowserCDPNotActive(
                f"Il browser e' in esecuzione ma senza supporto CDP "
                f"(porta {port} non risponde).\n\n"
                f"Il browser e' stato avviato prima che FSE Processor potesse "
                f"configurare il supporto CDP nel registro di Windows.\n\n"
                f"Per risolvere:\n"
                f"  1. Chiudi MANUALMENTE il browser (tutte le finestre)\n"
                f"  2. Riapri il browser normalmente\n"
                f"  3. Riprova il download\n\n"
                f"Il registro e' gia' configurato: ogni futuro avvio del "
                f"browser includera' automaticamente il supporto CDP.",
                process_name=process_name,
                exe_path=exe_path,
                port=port,
            )

        # 4. Browser NOT running → launch it with CDP
        self._logger.debug(f"[CDP] Browser non in esecuzione, exe_path={exe_path}")
        if exe_path and Path(exe_path).exists():
            self._logger.info(f"Browser non in esecuzione, lancio con CDP: {exe_path}")
            _launch_browser_with_cdp(exe_path, port, logger=self._logger)

            # Wait for CDP port to become available first
            port_ready = False
            elapsed = 0.0
            while elapsed < CDP_CONNECT_TIMEOUT:
                if _is_cdp_port_available(port):
                    port_ready = True
                    break
                time.sleep(CDP_CONNECT_POLL)
                elapsed += CDP_CONNECT_POLL

            if not port_ready:
                raise ConnectionError(
                    f"Browser lanciato ma la porta CDP {port} non risponde "
                    f"entro {CDP_CONNECT_TIMEOUT} secondi."
                )

            # Let Edge finish creating internal targets (Copilot sidebar, extensions, etc.)
            # before cleanup. Without this delay, _cleanup_cdp_targets misses late-arriving
            # "other" targets, and connect_over_cdp deadlocks on them.
            self._logger.debug("[CDP] Porta attiva, attesa stabilizzazione browser...")
            time.sleep(3)

            # Now cleanup + connect (with one retry if deadlock occurs)
            last_error = None
            for attempt in range(1, 3):
                try:
                    self._connect_cdp(endpoint, port)
                    self._logger.info(
                        f"Connesso al browser lanciato (tentativo {attempt})"
                    )
                    return
                except ConnectionError as e:
                    last_error = e
                    self._logger.warning(
                        f"Connessione al browser lanciato fallita (tentativo {attempt}): {e}"
                    )
                    if attempt < 2:
                        # Retry: cleanup again in case new targets appeared
                        time.sleep(2)

            raise ConnectionError(
                f"Browser lanciato ma la connessione CDP sulla porta {port} "
                f"non e' riuscita.\nUltimo errore: {last_error}"
            )

        # 5. No browser found, cannot launch
        self._logger.debug("[CDP] Nessun browser rilevato")
        raise ConnectionError(
            f"Impossibile connettersi via CDP alla porta {port}.\n"
            f"Nessun browser Chromium rilevato. Installa Microsoft Edge (Chromium) "
            f"o Google Chrome, oppure avvia manualmente il browser con:\n"
            f"  --remote-debugging-port={port}"
        )

    def restart_browser_with_cdp(self, process_name: str, exe_path: str, port: int) -> None:
        """Kill the browser and relaunch it with CDP, then connect.

        Should only be called after user consent (e.g. from GUI dialog).
        Ensures CDP registry flag is applied before killing, and uses
        --restore-last-session to preserve the user's open tabs.
        """
        # Ensure CDP registry is applied BEFORE killing, so the restarted
        # browser (and all future launches) will have CDP active.
        browser_info = detect_default_browser()
        if browser_info and browser_info.get("cdp_compatible", True):
            try:
                enable_cdp_in_registry(browser_info["progid"], port)
                self._logger.debug(
                    f"[CDP] Registro verificato/aggiornato prima del riavvio"
                )
            except Exception as e:
                self._logger.debug(f"[CDP] Impossibile aggiornare registro: {e}")

        self._logger.info(f"Riavvio del browser con CDP (porta {port})...")
        _kill_browser_processes(process_name, logger=self._logger)
        _launch_browser_with_cdp(exe_path, port, restore_session=True, logger=self._logger)

        endpoint = f"http://127.0.0.1:{port}"

        # Phase 1: Wait for CDP port to respond
        elapsed = 0.0
        while elapsed < CDP_CONNECT_TIMEOUT:
            if _is_cdp_port_available(port):
                break
            time.sleep(CDP_CONNECT_POLL)
            elapsed += CDP_CONNECT_POLL
        else:
            raise ConnectionError(
                f"Browser riavviato ma la porta CDP {port} non risponde "
                f"entro {CDP_CONNECT_TIMEOUT} secondi."
            )

        # Phase 2: Let Edge finish spawning internal targets
        self._logger.debug("[CDP] Porta attiva dopo riavvio, attesa stabilizzazione...")
        time.sleep(3)

        # Phase 3: Connect with retry (2 attempts)
        last_error = None
        for attempt in range(1, 3):
            try:
                self._connect_cdp(endpoint, port)
                self._logger.info(
                    f"Connesso al browser riavviato (tentativo {attempt})"
                )
                return
            except ConnectionError as e:
                last_error = e
                self._logger.warning(
                    f"Connessione dopo riavvio fallita (tentativo {attempt}): {e}"
                )
                if attempt < 2:
                    time.sleep(2)

        raise ConnectionError(
            f"Browser riavviato ma la connessione CDP sulla porta {port} "
            f"non e' riuscita.\nUltimo errore: {last_error}"
        )

    def _connect_cdp(self, endpoint: str, port: int) -> None:
        """Connect to a browser via CDP and set up context/page."""
        # Multi-pass cleanup: Edge recreates internal targets (newtab, Copilot)
        # after closure. Loop until stable (no new targets found).
        MAX_CLEANUP_PASSES = 5
        for pass_num in range(1, MAX_CLEANUP_PASSES + 1):
            closed = _cleanup_cdp_targets(port, logger=self._logger)
            if closed == 0:
                break
            self._logger.debug(
                f"[CDP] Cleanup pass {pass_num}: chiusi {closed} target, "
                f"attesa stabilizzazione..."
            )
            time.sleep(1.5)
        else:
            self._logger.warning(
                f"[CDP] Target problematici ancora presenti dopo "
                f"{MAX_CLEANUP_PASSES} passaggi di cleanup. Tentativo connessione comunque."
            )

        # Connect directly on this thread — MUST be same thread as sync_playwright().start()
        # Playwright's 15s timeout is sufficient after aggressive multi-pass cleanup.
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                endpoint, timeout=15000
            )
        except Exception as e:
            raise ConnectionError(
                f"Impossibile connettersi al browser su {endpoint}. "
                f"Assicurati che il browser sia avviato con "
                f"--remote-debugging-port={port}\n"
                f"Errore: {e}"
            )

        # Use the default context (carries existing cookies/session)
        contexts = self._browser.contexts
        self._logger.debug(f"[CDP] Contesti trovati: {len(contexts)}")
        if not contexts:
            raise ConnectionError(
                "Browser connesso via CDP ma nessun contesto trovato. "
                "Apri almeno una finestra nel browser."
            )
        self._context = contexts[0]
        self._attached = True

        # Log all pages and their URLs for diagnostics
        for i, p in enumerate(self._context.pages):
            try:
                try:
                    url = p.evaluate("window.location.href")
                except Exception:
                    url = p.url
                self._logger.debug(f"[CDP] Tab {i}: {url}")
            except Exception as e:
                self._logger.debug(f"[CDP] Tab {i}: non accessibile ({e})")

        # Cerca pagina SISS autenticata tra i tab esistenti
        siss_page = self._find_authenticated_siss_page()
        if siss_page:
            self._page = siss_page
            self._owned_page = None  # Non chiudere su stop() — non è nostra
            self._logger.info(
                f"Pagina SISS autenticata riutilizzata: {self._get_real_url(siss_page)}"
            )
        else:
            # Nessuna sessione attiva — cattura sessionStorage e crea tab di lavoro
            siss_storage = self._capture_siss_session_storage()
            self._logger.debug(f"[CDP] SISS sessionStorage catturato: {siss_storage is not None}")

            self._page = self._find_reusable_page()
            if self._page is None:
                self._logger.debug("[CDP] Nessun tab riutilizzabile, creazione nuovo tab")
                self._page = self._context.new_page()
            self._owned_page = self._page  # Track: this is OUR tab, safe to close

            if siss_storage:
                self._inject_siss_session(siss_storage)

        self._page.set_default_timeout(self._config.page_timeout)
        self._logger.info(
            f"Connesso a browser esistente (CDP porta {port}, "
            f"{len(self._context.pages)} tab totali)"
        )

    def _get_real_url(self, page: Page | None = None) -> str:
        """Get the real current URL via JS evaluation (page.url can be stale in CDP)."""
        p = page or self._page
        try:
            return p.evaluate("window.location.href")
        except Exception:
            return p.url

    def _is_siss_authenticated(self, page: Page | None = None) -> bool:
        """Check if a page shows an authenticated SISS session (not SSO login)."""
        url = self._get_real_url(page)
        return (
            ("operatorisiss" in url or "servizirl" in url)
            and "idpcrlmain" not in url
            and "ssoauth" not in url
        )

    def _find_authenticated_siss_page(self) -> Page | None:
        """Search all browser tabs for one with an active SISS session."""
        if not self._context:
            return None
        for page in self._context.pages:
            try:
                if self._is_siss_authenticated(page):
                    return page
            except Exception:
                continue
        return None

    def check_headless_auth(self) -> bool:
        """Check if a headless browser has an active SISS session.

        Returns True if headless is OFF or a valid session exists.
        Returns False if headless is ON and no session was found
        (meaning the user cannot login because the window is invisible).
        """
        if not self._config.headless:
            return True

        # First check existing tabs for a valid session
        if self._find_authenticated_siss_page():
            self._logger.info(
                "Headless: sessione SISS attiva trovata su tab esistente"
            )
            return True

        # Navigate to FSE portal and check if cookies/session carry over
        try:
            page = self._page or (
                self._context.pages[0] if self._context.pages else None
            )
            if page is None:
                page = self._context.new_page()
            page.goto(FSE_BASE_URL, wait_until="networkidle", timeout=15000)
            if self._is_siss_authenticated(page):
                self._logger.info(
                    "Headless: sessione SISS attiva dopo navigazione FSE"
                )
                return True
        except Exception as e:
            self._logger.debug(f"Headless auth check fallito: {e}")

        self._logger.warning(
            "Headless attivo ma nessuna sessione SISS valida rilevata"
        )
        return False

    def _find_reusable_page(self) -> Page | None:
        """Find an existing blank/new-tab page to reuse instead of creating a new one.

        Reusing an existing tab avoids the sessionStorage isolation that comes
        with new_page() — though we still inject sessionStorage via init script
        as a safety net.
        """
        BLANK_URLS = ("about:blank", "")
        for page in self._context.pages:
            try:
                try:
                    url = page.evaluate("window.location.href").lower()
                except Exception:
                    url = page.url.lower()
                self._logger.debug(f"  Tab candidata: {url}")
                if url in BLANK_URLS or "newtab" in url or "ntp" in url:
                    self._logger.info(f"Tab esistente riutilizzato: {url}")
                    return page
            except Exception as e:
                self._logger.debug(f"  Tab non accessibile: {e}")
                continue
        return None

    def _capture_siss_session_storage(self) -> dict | None:
        """Extract sessionStorage from an existing tab on the SISS domain."""
        for page in self._context.pages:
            try:
                try:
                    url = page.evaluate("window.location.href")
                except Exception:
                    url = page.url
                if "operatorisiss" in url or "servizirl" in url:
                    data = page.evaluate(
                        "() => {"
                        "  const s = {};"
                        "  for (let i = 0; i < sessionStorage.length; i++) {"
                        "    const k = sessionStorage.key(i);"
                        "    s[k] = sessionStorage.getItem(k);"
                        "  }"
                        "  return s;"
                        "}"
                    )
                    if data:
                        self._logger.info(
                            f"SessionStorage SISS catturato ({len(data)} chiavi)"
                        )
                        return data
                    else:
                        self._logger.debug(f"Tab SISS trovato ma sessionStorage vuoto: {url}")
            except Exception as e:
                self._logger.debug(f"Errore cattura sessionStorage: {e}")
                continue
        self._logger.debug("Nessun tab SISS trovato per cattura sessionStorage")
        return None

    def _inject_siss_session(self, storage: dict) -> None:
        """Inject SISS sessionStorage into the automation page via init script.

        Uses add_init_script so the data is available before any SPA code runs.
        Only injects when sessionStorage is empty (first load on that origin)
        to avoid overwriting fresh tokens after a manual re-login.
        """
        storage_json = json.dumps(storage)
        self._page.add_init_script(
            "() => {"
            "  if ((location.hostname.includes('operatorisiss') ||"
            "       location.hostname.includes('servizirl')) &&"
            "      sessionStorage.length === 0) {"
            "    try {"
            "      const data = " + storage_json + ";"
            "      Object.entries(data).forEach(([k, v]) => {"
            "        try { sessionStorage.setItem(k, v); } catch(e) {}"
            "      });"
            "    } catch(e) {}"
            "  }"
            "}"
        )
        self._logger.info("Init script SISS sessionStorage iniettato nel tab di automazione")

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
                result = subprocess.run(
                    [node_exe, cli_js, "install", "--with-deps", "chromium"],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip()
                    raise RuntimeError(
                        f"Installazione Chromium fallita (exit {result.returncode}). "
                        f"Dettagli: {detail}"
                    )
            else:
                raise RuntimeError(
                    f"Driver Playwright non trovato nel bundle. "
                    f"Cercato node.exe in: {node_exe}"
                )
        else:
            # Dev mode: use Python
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                check=True,
            )

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

    def _reset_download_behavior(self) -> None:
        """Restore the browser's native download handling.

        Playwright intercepts ALL downloads via Browser.setDownloadBehavior
        (CDP) when connected.  This means manual downloads by the user get
        stuck in temporary files because nobody calls download.save_as().

        Call this after every automated operation to ensure the user can
        always download files manually.  Before an automated download,
        call _enable_download_interception() to temporarily re-enable
        Playwright's download manager.
        """
        if not self._page or not self._context:
            return
        try:
            cdp = self._context.new_cdp_session(self._page)
            cdp.send("Browser.setDownloadBehavior", {"behavior": "default"})
            cdp.detach()
            self._logger.debug("Download behavior del browser ripristinato (manuale abilitato)")
        except Exception:
            pass

    def stop(self) -> None:
        if self._attached:
            # CDP mode: restore browser state, close only OUR tab, leave browser running.
            # Never close tabs we didn't create (e.g. Millewin's SISS session).
            self._reset_download_behavior()

            # Close the tab we created/found during _connect_cdp
            page_to_close = self._owned_page
            if page_to_close:
                try:
                    page_to_close.close()
                except Exception:
                    pass

            # If self._page was switched to a borrowed tab (e.g. by
            # wait_for_manual_login finding an existing SISS tab),
            # do NOT close it — it belongs to other apps (Millewin etc.)
            if self._page and self._page != page_to_close:
                self._logger.debug(
                    "Tab corrente diverso da quello creato — non chiuso "
                    "(appartiene ad altre applicazioni)"
                )

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
        self._owned_page = None
        self._attached = False
        self._logger.info("Browser chiuso")

    def _auto_login_firma_remota(self) -> bool:
        """Automate SSO login via Firma Remota.

        Fills username and password from config, requests OTP via callback,
        then submits the form and waits for redirect to SISS portal.

        Returns True if login succeeded, False otherwise.
        """
        username = self._config.sso_username
        password = self._config.sso_password

        if not username or not password:
            self._logger.debug("Login automatico: credenziali SSO non configurate")
            return False

        if not self._otp_callback:
            self._logger.debug("Login automatico: nessun callback OTP disponibile")
            return False

        try:
            current_url = self._get_real_url().lower()

            # If we're already on the SSO page, click Firma Remota
            if "idpcrlmain" in current_url or "ssoauth" in current_url:
                self._logger.info("Login automatico: pagina SSO rilevata, selezione Firma Remota...")
                # Check if we're already on the Firma Remota form (loginOtp.jsp)
                if "loginotp" not in current_url:
                    firma_remota = self._page.locator(
                        "xpath=//strong[normalize-space()='Accesso con Firma Remota']"
                    )
                    firma_remota.wait_for(state="visible", timeout=10000)
                    firma_remota.click()
                    self._page.wait_for_load_state("domcontentloaded")
            else:
                # Navigate to FSE which will redirect to SSO
                self._logger.info("Login automatico: navigazione al portale FSE...")
                self._page.goto(FSE_BASE_URL, wait_until="domcontentloaded", timeout=30000)

                # If we're now authenticated, no need to login
                if self._is_siss_authenticated():
                    self._logger.info("Login automatico: sessione gia' attiva")
                    return True

                # Click "Accedi" if present
                try:
                    accedi = self._page.get_by_role("button", name="Accedi")
                    if accedi.is_visible(timeout=3000):
                        accedi.click()
                        self._page.wait_for_load_state("domcontentloaded")
                except Exception:
                    pass

                # Now we should be on the SSO page
                if "idpcrlmain" not in self._get_real_url().lower():
                    self._logger.warning("Login automatico: redirect SSO non avvenuto")
                    return False

                # Select Firma Remota
                self._logger.info("Login automatico: selezione 'Accesso con Firma Remota'...")
                firma_remota = self._page.locator(
                    "xpath=//strong[normalize-space()='Accesso con Firma Remota']"
                )
                firma_remota.wait_for(state="visible", timeout=10000)
                firma_remota.click()
                self._page.wait_for_load_state("domcontentloaded")

            self._logger.info("Login automatico: compilazione credenziali...")

            # Dismiss cookie consent if present
            try:
                cookie_btn = self._page.locator("button[aria-label='dismiss cookie message']")
                if cookie_btn.is_visible(timeout=1000):
                    cookie_btn.click()
            except Exception:
                pass

            # Fill username and password
            self._page.get_by_placeholder("Username").fill(username)
            self._page.get_by_placeholder("Password").fill(password)

            # Request OTP from user via callback
            self._logger.info("Login automatico: in attesa del codice OTP dall'utente...")
            otp = self._otp_callback()
            if not otp:
                self._logger.info("Login automatico: OTP annullato dall'utente")
                return False

            # Fill OTP and submit
            self._page.get_by_placeholder("Otp").fill(otp)
            self._logger.info("Login automatico: invio credenziali...")
            self._page.get_by_role("button", name="Invia").click()

            # Wait for redirect — either back to SISS portal or error
            self._page.wait_for_load_state("networkidle", timeout=30000)

            # Check if login succeeded (redirected back to SISS portal)
            if self._is_siss_authenticated():
                self._logger.info(
                    f"Login automatico completato: {self._get_real_url()}"
                )
                return True

            # Check for SSO error message
            final_url = self._get_real_url().lower()
            if "idpcrlmain" in final_url or "ssoauth" in final_url:
                # Still on SSO page — login failed
                try:
                    error_el = self._page.locator("p.errore, .errMsg, [class*='error']")
                    if error_el.count() > 0:
                        error_text = error_el.first.text_content()
                        self._logger.warning(
                            f"Login automatico fallito: {error_text}"
                        )
                    else:
                        self._logger.warning("Login automatico fallito: credenziali o OTP non validi")
                except Exception:
                    self._logger.warning("Login automatico fallito: credenziali o OTP non validi")
                return False

            self._logger.warning(
                f"Login automatico: stato incerto, URL finale: {self._get_real_url()}"
            )
            return False

        except Exception as e:
            self._logger.warning(f"Login automatico fallito: {e}")
            return False

    def wait_for_manual_login(self, stop_event: threading.Event | None = None) -> None:
        """Navigate to the FSE portal and wait for the user to complete SSO login.

        First checks if an active SISS session already exists (e.g. from Millewin)
        to avoid unnecessary manual login that would invalidate other apps' sessions.
        """
        import time

        # ── Step 0: Check if ANY existing tab already has an active SISS session ──
        existing_siss = self._find_authenticated_siss_page()
        if existing_siss:
            siss_url = self._get_real_url(existing_siss)
            self._logger.info(
                f"Sessione SISS attiva trovata: {siss_url} — riutilizzo diretto"
            )
            if existing_siss != self._page:
                self._page = existing_siss
            self._page.bring_to_front()
            return

        # ── Step 1: Navigate OUR page to FSE, authenticate via existing cookies ──
        self._logger.info("Navigazione al portale FSE per verifica sessione...")
        self._page.goto(FSE_BASE_URL, wait_until="domcontentloaded", timeout=30000)
        self._page.bring_to_front()

        # Try clicking "Accedi" to trigger SSO redirect
        try:
            accedi = self._page.get_by_role("button", name="Accedi")
            if accedi.is_visible(timeout=5000):
                accedi.click()
                self._page.wait_for_load_state("networkidle")
        except Exception:
            pass  # Button might not be there, that's ok

        # ── Step 2: Check if we ended up on an authenticated page (no SSO redirect) ──
        if self._is_siss_authenticated():
            self._logger.info(
                f"Sessione SISS attiva: {self._get_real_url()} — "
                f"login manuale non necessario"
            )
            return

        # Also check other tabs — if another tab is authenticated, the cookies
        # are valid but our page didn't pick them up (SPA quirk). Try refreshing.
        auth_page = self._find_authenticated_siss_page()
        if auth_page and auth_page != self._page:
            self._logger.info(
                "Sessione SISS attiva su altro tab ma non sul nostro — "
                "tentativo refresh..."
            )
            self._page.reload(wait_until="networkidle")
            if self._is_siss_authenticated():
                self._logger.info(
                    f"Sessione attiva dopo refresh: {self._get_real_url()}"
                )
                return

        # ── Step 3: Attempt auto-login via Firma Remota if credentials available ──
        if self._config.sso_username and self._config.sso_password and self._otp_callback:
            self._logger.info("Tentativo di login automatico via Firma Remota...")
            if self._auto_login_firma_remota():
                return

        # ── Step 4: No active session found — wait for manual login ──
        self._logger.info(
            "LOGIN MANUALE RICHIESTO - Completa l'accesso nel browser. "
            "L'app proseguira' automaticamente dopo il login."
        )

        # Poll until we're back on the FSE portal (not on the SSO/login page)
        max_wait = 300  # 5 minutes max
        elapsed = 0
        while elapsed < max_wait:
            # Check for stop request
            if stop_event is not None and stop_event.is_set():
                self._logger.info("Attesa login interrotta dall'utente")
                raise InterruptedError("Attesa login interrotta dall'utente")

            try:
                # First check if OUR page is now authenticated (user logged in on it)
                if self._is_siss_authenticated():
                    self._logger.info(f"Login rilevato su nostro tab: {self._get_real_url()}")
                    break

                # Check other pages — if the user logged in on a different tab,
                # cookies are now valid. Navigate our page with those cookies.
                found_elsewhere = False
                for page in self._context.pages:
                    if page == self._page:
                        continue
                    try:
                        if self._is_siss_authenticated(page):
                            self._logger.info(
                                f"Login rilevato su altro tab, navigazione nostro tab con cookie valide..."
                            )
                            self._page.goto(FSE_BASE_URL, wait_until="networkidle", timeout=15000)
                            if self._is_siss_authenticated():
                                self._logger.info(f"Login rilevato: {self._get_real_url()}")
                                found_elsewhere = True
                            break
                    except Exception:
                        continue
                if found_elsewhere:
                    break

                # Not found yet - log progress every 30s (no navigation to avoid
                # disrupting SSO login the user may be completing)
                if elapsed > 0 and elapsed % 30 == 0:
                    self._logger.info(
                        f"Attesa login [{elapsed}s/{max_wait}s] — "
                        f"completa l'accesso SSO nel browser..."
                    )
                time.sleep(2)
                elapsed += 2
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

    def _check_consent_service_error(self, codice_fiscale: str) -> None:
        """Raise if the page shows 'Errore nel servizio di consenso'."""
        try:
            err = self._page.locator("text=Errore nel servizio di consenso")
            if err.is_visible(timeout=500):
                raise RuntimeError(
                    f"Errore nel servizio di consenso per {codice_fiscale}. "
                    "Paziente saltato."
                )
        except RuntimeError:
            raise
        except Exception:
            pass

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

                # Session validity check: the overlay "Identificazione del
                # cittadino in corso" (ngx-spinner) should appear after
                # clicking Cerca.  If it doesn't, the page session expired.
                spinner = self._page.locator("ngx-spinner .overlay")
                try:
                    spinner.wait_for(state="visible", timeout=3000)
                except Exception:
                    self._logger.warning(
                        "Pagina di ricerca scaduta: overlay 'Identificazione "
                        "del cittadino in corso' non comparso. Refresh automatico..."
                    )
                    self._page.reload(wait_until="networkidle")
                    self._wait_for_spinner()
                    # Retry: fill CF and click Cerca once more
                    cf_input = self._page.locator("#inputcf")
                    if cf_input.is_visible(timeout=5000):
                        cf_input.fill(codice_fiscale)
                        self._wait_for_spinner()
                        cerca = self._page.get_by_role("button", name="Cerca")
                        if cerca.is_visible(timeout=3000):
                            cerca.click()

                self._page.wait_for_load_state("networkidle")
                self._wait_for_spinner()

                # Check for consent service error before assuming session expired
                self._check_consent_service_error(codice_fiscale)

                # Post-search session check: the spinner may have briefly
                # flashed (Angular started processing) but the expired
                # session caused an instant redirect back to the search
                # form.  Detect this by checking if #inputcf reappeared.
                cf_input_post = self._page.locator("#inputcf")
                if cf_input_post.is_visible(timeout=1500):
                    self._logger.warning(
                        "Pagina di ricerca scaduta: la pagina e' tornata "
                        "al form di ricerca dopo il click su 'Cerca' "
                        "(sessione Angular scaduta). Refresh automatico..."
                    )
                    self._page.reload(wait_until="networkidle")
                    self._wait_for_spinner()
                    # Retry: fill CF and click Cerca once more
                    cf_input_post = self._page.locator("#inputcf")
                    if cf_input_post.is_visible(timeout=5000):
                        cf_input_post.fill(codice_fiscale)
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
        self._logger.info(f"Pagina referti caricata: {self._get_real_url()}")

    def navigate_for_millewin(self, codice_fiscale: str,
                              stop_event: threading.Event | None = None,
                              on_page_expired: object | None = None) -> None:
        """Navigate to the FSE documents page for a patient from Millewin.

        Args:
            on_page_expired: Optional callable invoked when the search page
                session is detected as expired (before auto-refresh).

        Handles these scenarios:
        1. Page already showing documents for same patient → do nothing
        2. Page showing documents for different patient → click 'Identifica altro cittadino', re-navigate
        3. Page on CF input form → fill and navigate
        4. Session expired → refresh and retry
        5. No FSE page open → open new one
        """
        # Step 1: Find or open an FSE page
        fse_page = self._find_authenticated_siss_page()

        if fse_page:
            self._page = fse_page
            self._page.set_default_timeout(self._config.page_timeout)
            self._page.bring_to_front()
            self._logger.info(f"Pagina FSE esistente trovata: {self._get_real_url()}")
        else:
            self._logger.info("Nessuna pagina FSE trovata, apertura nuova pagina...")
            self._page = self._context.new_page()
            self._page.set_default_timeout(self._config.page_timeout)
            self._page.goto(FSE_BASE_URL, wait_until="networkidle")

            # Check if we landed on SSO login
            if not self._is_siss_authenticated():
                self.wait_for_manual_login(stop_event)

        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Workflow interrotto dall'utente")

        self._wait_for_spinner()

        # Step 2: Check current state of the page
        existing_cf = None
        try:
            existing_cf = self._page.evaluate(
                'document.querySelector("span:nth-child(2) span:nth-child(1)")?.textContent?.trim()'
            )
        except Exception:
            pass

        if existing_cf:
            existing_cf_upper = existing_cf.strip().upper()
            if existing_cf_upper == codice_fiscale.upper():
                self._logger.info("Paziente gia' aperto nel FSE, nessuna azione necessaria")
                return
            else:
                self._logger.info(f"Paziente diverso nel FSE ({existing_cf_upper}), cambio paziente...")
                try:
                    self._page.locator('a[href="#/"]').click()
                    self._page.wait_for_load_state("networkidle")
                    self._wait_for_spinner()
                except Exception as e:
                    self._logger.warning(f"Click 'Identifica altro cittadino' fallito: {e}, navigazione diretta...")
                    self._page.goto(FSE_BASE_URL, wait_until="networkidle")
                    self._wait_for_spinner()

        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Workflow interrotto dall'utente")

        # Step 3: Fill CF and navigate (with session expiry handling)
        max_retries = 2
        for attempt in range(max_retries):
            cf_input = self._page.locator("#inputcf")
            if not cf_input.is_visible(timeout=5000):
                self._logger.warning("Campo #inputcf non visibile, navigazione a pagina iniziale FSE...")
                self._page.goto(FSE_BASE_URL, wait_until="networkidle")
                self._wait_for_spinner()
                # Check for SSO redirect
                if not self._is_siss_authenticated():
                    self.wait_for_manual_login(stop_event)
                    self._wait_for_spinner()
                cf_input = self._page.locator("#inputcf")
                if not cf_input.is_visible(timeout=5000):
                    raise RuntimeError("Campo codice fiscale non trovato nella pagina FSE")

            cf_input.fill(codice_fiscale)
            self._logger.info(f"Codice fiscale inserito: {codice_fiscale}")
            self._wait_for_spinner()

            cerca = self._page.get_by_role("button", name="Cerca")
            if not cerca.is_visible(timeout=3000):
                raise RuntimeError("Pulsante 'Cerca' non trovato")

            cerca.click()

            # Session validity check: clicking "Cerca" on a valid page
            # triggers the overlay "Identificazione del cittadino in corso"
            # (ngx-spinner).  If the overlay does NOT appear, the page
            # session has expired and a refresh is needed.
            spinner = self._page.locator("ngx-spinner .overlay")
            try:
                spinner.wait_for(state="visible", timeout=3000)
            except Exception:
                # Overlay not shown → page session expired
                if on_page_expired is not None:
                    on_page_expired()
                if attempt < max_retries - 1:
                    self._logger.warning(
                        "Pagina di ricerca scaduta: overlay 'Identificazione "
                        "del cittadino in corso' non comparso dopo click su "
                        "'Cerca'. Aggiornamento automatico della pagina..."
                    )
                    self._page.reload(wait_until="networkidle")
                    self._wait_for_spinner()
                    continue
                else:
                    raise RuntimeError(
                        "Pagina di ricerca scaduta. Dopo aver cliccato 'Cerca' "
                        "l'overlay 'Identificazione del cittadino in corso' non "
                        "compare, segno che la sessione della pagina e' scaduta. "
                        "Aggiornare manualmente la pagina e riprovare."
                    )

            # Spinner appeared → search started, wait for completion
            self._wait_for_spinner()

            # Check for consent service error before assuming session expired
            self._check_consent_service_error(codice_fiscale)

            # Post-search session check: the spinner may have briefly
            # flashed (Angular started processing) but the expired
            # session caused an instant redirect back to the search
            # form.  Detect this by checking if #inputcf reappeared.
            cf_input_post = self._page.locator("#inputcf")
            if cf_input_post.is_visible(timeout=1500):
                if on_page_expired is not None:
                    on_page_expired()
                if attempt < max_retries - 1:
                    self._logger.warning(
                        "Pagina di ricerca scaduta: la pagina e' tornata "
                        "al form di ricerca dopo il click su 'Cerca' "
                        "(sessione Angular scaduta). Aggiornamento "
                        "automatico della pagina..."
                    )
                    self._page.reload(wait_until="networkidle")
                    self._wait_for_spinner()
                    continue
                else:
                    raise RuntimeError(
                        "Pagina di ricerca scaduta. La pagina torna al "
                        "form di ricerca dopo aver cliccato 'Cerca', "
                        "segno che la sessione Angular e' scaduta. "
                        "Aggiornare manualmente la pagina e riprovare."
                    )

            # Wait for next UI element (Accedi button or Referti tab)
            try:
                self._page.locator("button.btn-xlarge, :text-is('Referti')").first.wait_for(
                    state="visible", timeout=15000,
                )
            except Exception:
                raise RuntimeError(
                    "Timeout in attesa dei risultati ricerca paziente."
                )

            self._page.wait_for_load_state("networkidle")
            self._wait_for_spinner()
            break  # Success

        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Workflow interrotto dall'utente")

        # Step 4: Click "Accedi" if visible
        accedi_btn = self._page.locator("button.btn-xlarge")
        if accedi_btn.is_visible(timeout=3000):
            accedi_btn.click()
            self._page.wait_for_load_state("networkidle")
            self._wait_for_spinner()

        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Workflow interrotto dall'utente")

        # Step 5: Click "Referti" tab
        self._logger.info("Ricerca tab 'Referti' nella pagina...")
        referti = self._page.get_by_text("Referti", exact=True).first
        if referti.is_visible(timeout=5000):
            referti.click()
        else:
            self._page.get_by_text("Referti").first.click()
        self._page.wait_for_load_state("networkidle")
        self._wait_for_spinner()

        self._logger.info("Pagina documenti pronta")

        # Restore default download behavior so the user can download
        # files manually from the browser while inspecting the page.
        self._reset_download_behavior()

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
            self._logger.start_progress("Accesso al fascicolo sanitario")
            self._page.goto(fse_link, wait_until="networkidle")
            self._wait_for_spinner()

            # Check if we need to click "Accedi" (session might already be active)
            accedi = self._page.get_by_role("button", name="Accedi")
            if accedi.is_visible(timeout=3000):
                accedi.click()
                self._page.wait_for_load_state("networkidle")
                self._wait_for_spinner()

            # Check if we got redirected to SSO (session expired)
            if not self._is_siss_authenticated():
                self._logger.stop_progress()
                import time

                # Try auto-login first
                if self._config.sso_username and self._config.sso_password and self._otp_callback:
                    self._logger.info("Sessione SSO scaduta - tentativo login automatico...")
                    if self._auto_login_firma_remota():
                        self._page.goto(fse_link, wait_until="networkidle", timeout=15000)
                        self._wait_for_spinner()

                # Fall back to manual login if auto-login didn't succeed
                if not self._is_siss_authenticated():
                    self._logger.warning(
                        "Sessione SSO scaduta - completa il login nel browser. "
                        "L'app proseguira' automaticamente."
                    )
                    max_wait = 60
                    elapsed = 0
                    while elapsed < max_wait:
                        try:
                            if self._is_siss_authenticated():
                                break
                            # Check if user logged in on another tab → cookies valid
                            auth_page = self._find_authenticated_siss_page()
                            if auth_page and auth_page != self._page:
                                self._logger.info("Login su altro tab, navigazione nostro tab...")
                                self._page.goto(fse_link, wait_until="networkidle", timeout=15000)
                                if self._is_siss_authenticated():
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
            self._logger.stop_progress()

        except Exception as e:
            self._logger.stop_progress()
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
            self._logger.debug(f"HTML prima riga dati: {first_row_html[:500]}")

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
                        disciplina=tipo_text, skipped=True, download_path=None, error=None,
                        date_text=date_text,
                    ))
                    continue

                # Download document
                result = self._download_document(row_idx, tipo_text, patient_name, visualizza_col, data_rows,
                                                 date_text=date_text)
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
        if not self._is_siss_authenticated():
            import time

            # Try auto-login first
            if self._config.sso_username and self._config.sso_password and self._otp_callback:
                self._logger.info("Sessione SSO scaduta - tentativo login automatico...")
                if self._auto_login_firma_remota():
                    self._page.goto(fse_link, wait_until="networkidle", timeout=15000)
                    self._wait_for_spinner()

            # Fall back to manual login if auto-login didn't succeed
            if not self._is_siss_authenticated():
                self._logger.warning(
                    "Sessione SSO scaduta - completa il login nel browser. "
                    "L'app proseguira' automaticamente."
                )
                max_wait = 60
                elapsed = 0
                while elapsed < max_wait:
                    try:
                        if self._is_siss_authenticated():
                            break
                        # Check if user logged in on another tab → cookies valid
                        auth_page = self._find_authenticated_siss_page()
                        if auth_page and auth_page != self._page:
                            self._logger.info("Login su altro tab, navigazione nostro tab...")
                            self._page.goto(fse_link, wait_until="networkidle", timeout=15000)
                            if self._is_siss_authenticated():
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

    def scan_patient_enti(self, codice_fiscale: str) -> tuple[list[str], list[str]]:
        """Navigate to the patient page and return sorted unique Ente and Disciplina values.

        Returns (enti, discipline) where discipline only includes values from
        REFERTO SPECIALISTICO rows, excluding blacklisted disciplines.
        """
        if not self._is_alive():
            self._restart()

        fse_link = f"{FSE_BASE_URL}#/?codiceFiscale={codice_fiscale}"
        self._logger.info(f"Scansione enti per {codice_fiscale}: {fse_link}")

        self._navigate_and_login(fse_link, codice_fiscale)

        referti_table = self._page.locator("table:has(th:has-text('Tipologia documento'))")
        referti_table.wait_for(state="attached", timeout=10000)

        headers = referti_table.locator("thead th")
        header_count = headers.count()
        header_texts = [headers.nth(j).inner_text().strip() for j in range(header_count)]

        ente_col = tipo_col = disc_col = None
        for idx, h in enumerate(header_texts):
            h_upper = h.upper()
            if "ENTE" in h_upper or "STRUTTURA" in h_upper:
                ente_col = idx
            elif "TIPOLOGIA" in h_upper:
                tipo_col = idx
            elif "DISCIPLINA" in h_upper:
                disc_col = idx

        if ente_col is None:
            self._logger.warning(f"Colonna 'Ente/Struttura' non trovata: {header_texts}")
            return [], []

        data_rows = referti_table.locator("tbody tr:has(td)")
        row_count = data_rows.count()
        self._logger.info(f"Scansione enti: {row_count} righe trovate")

        ente_set: set[str] = set()
        disc_set: set[str] = set()
        for i in range(row_count):
            cells = data_rows.nth(i).locator("td")
            cell_count = cells.count()
            if cell_count <= ente_col:
                continue
            ente_text = cells.nth(ente_col).inner_text().strip()
            if ente_text:
                ente_set.add(ente_text)
            # Collect disciplines only from REFERTO SPECIALISTICO rows
            if tipo_col is not None and disc_col is not None and cell_count > disc_col:
                tipo_text = cells.nth(tipo_col).inner_text().strip()
                if tipo_text.upper() == "REFERTO SPECIALISTICO":
                    disc_text = cells.nth(disc_col).inner_text().strip()
                    if disc_text and not _is_discipline_excluded(disc_text):
                        disc_set.add(disc_text)

        self._logger.info(f"Enti trovati: {sorted(ente_set)}")
        if disc_set:
            self._logger.info(f"Discipline trovate: {sorted(disc_set)}")
        return sorted(ente_set), sorted(disc_set)

    def list_patient_documents(self, codice_fiscale: str,
                               stop_event: threading.Event | None = None,
                               on_enti_found: callable | None = None,
                               on_discipline_found: callable | None = None) -> list[PatientDocumentInfo]:
        """Navigate to the FSE and read the document table without downloading anything.

        Returns a list of PatientDocumentInfo for every row in the table.
        """
        if stop_event is not None and stop_event.is_set():
            return []

        if not self._is_alive():
            self._restart()

        fse_link = f"{FSE_BASE_URL}#/?codiceFiscale={codice_fiscale}"
        self._logger.info(f"Navigazione FSE per elenco documenti: {fse_link}")

        self._navigate_and_login(fse_link, codice_fiscale)

        referti_table = self._page.locator("table:has(th:has-text('Tipologia documento'))")
        referti_table.wait_for(state="attached", timeout=10000)

        headers = referti_table.locator("thead th")
        header_count = headers.count()
        header_texts = [headers.nth(j).inner_text().strip() for j in range(header_count)]

        date_col = tipo_col = ente_col = disc_col = None
        for idx, h in enumerate(header_texts):
            h_upper = h.upper()
            if "DATA" in h_upper and date_col is None:
                date_col = idx
            elif "TIPOLOGIA" in h_upper:
                tipo_col = idx
            elif "ENTE" in h_upper or "STRUTTURA" in h_upper:
                ente_col = idx
            elif "DISCIPLINA" in h_upper:
                disc_col = idx

        if tipo_col is None:
            raise RuntimeError(f"Colonna 'Tipologia' non trovata nelle intestazioni: {header_texts}")

        data_rows = referti_table.locator("tbody tr:has(td)")
        row_count = data_rows.count()
        self._logger.info(f"Trovate {row_count} righe nella tabella referti")

        docs: list[PatientDocumentInfo] = []
        ente_set: set[str] = set()
        disc_set: set[str] = set()
        for i in range(row_count):
            cells = data_rows.nth(i).locator("td")
            cell_count = cells.count()
            if cell_count <= (tipo_col or 0):
                continue
            date_text = cells.nth(date_col).inner_text().strip() if date_col is not None else ""
            tipo_text = cells.nth(tipo_col).inner_text().strip()
            ente_text = cells.nth(ente_col).inner_text().strip() if ente_col is not None else ""
            disc_text = cells.nth(disc_col).inner_text().strip() if disc_col is not None and cell_count > disc_col else ""
            docs.append(PatientDocumentInfo(row_index=i, date_text=date_text, tipo_text=tipo_text,
                                            ente_text=ente_text, disciplina_text=disc_text))
            if ente_text:
                ente_set.add(ente_text)
            if disc_text and tipo_text.upper() == "REFERTO SPECIALISTICO" and not _is_discipline_excluded(disc_text):
                disc_set.add(disc_text)

        if on_enti_found and ente_set:
            on_enti_found(sorted(ente_set))
        if on_discipline_found and disc_set:
            on_discipline_found(sorted(disc_set))

        self._logger.info(f"Elencati {len(docs)} documenti")
        return docs

    def process_patient_all_dates(self, codice_fiscale: str,
                                  stop_event: threading.Event | None = None,
                                  allowed_types: set[str] | None = None,
                                  ente_filter: str = "",
                                  date_from: date | None = None,
                                  date_to: date | None = None,
                                  on_enti_found: callable | None = None,
                                  selected_row_indices: set[int] | None = None,
                                  discipline_filter: str = "",
                                  on_discipline_found: callable | None = None) -> list[DocumentResult]:
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
            self._logger.start_progress("Accesso al fascicolo sanitario")
            self._navigate_and_login(fse_link, codice_fiscale)
            self._logger.stop_progress()
        except Exception as e:
            self._logger.stop_progress()
            self._logger.error(f"Errore navigazione FSE per {codice_fiscale}: {e}")
            self._take_debug_screenshot(codice_fiscale)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        try:
            self._logger.start_progress("Caricamento tabella referti")
            referti_table = self._page.locator("table:has(th:has-text('Tipologia documento'))")
            referti_table.wait_for(state="attached", timeout=10000)
            self._logger.stop_progress()

            headers = referti_table.locator("thead th")
            header_count = headers.count()
            header_texts = [headers.nth(j).inner_text().strip() for j in range(header_count)]
            self._logger.info(f"Intestazioni tabella referti: {header_texts}")

            date_col = tipo_col = ente_col = visualizza_col = disc_col = None
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
                elif "DISCIPLINA" in h_upper:
                    disc_col = idx

            if tipo_col is None:
                raise RuntimeError(f"Colonna 'Tipologia' non trovata nelle intestazioni: {header_texts}")

            data_rows = referti_table.locator("tbody tr:has(td)")
            row_count = data_rows.count()
            self._logger.info(f"Trovate {row_count} righe dati nella tabella referti")

            if row_count == 0:
                return results

            # Phase 1: Scan all rows to extract data and collect unique enti/discipline
            self._logger.start_progress(f"Analisi {row_count} righe della tabella")
            row_info: list[tuple[int, str, str, str, str]] = []  # (index, date_text, tipo_text, ente_text, disc_text)
            ente_set: set[str] = set()
            disc_set: set[str] = set()
            for i in range(row_count):
                cells = data_rows.nth(i).locator("td")
                cell_count = cells.count()
                if cell_count <= (tipo_col or 0):
                    continue
                date_text = cells.nth(date_col).inner_text().strip() if date_col is not None else ""
                tipo_text = cells.nth(tipo_col).inner_text().strip()
                ente_text = cells.nth(ente_col).inner_text().strip() if ente_col is not None else ""
                disc_text = cells.nth(disc_col).inner_text().strip() if disc_col is not None and cell_count > disc_col else ""
                row_info.append((i, date_text, tipo_text, ente_text, disc_text))
                if ente_text:
                    ente_set.add(ente_text)
                if disc_text and tipo_text.upper() == "REFERTO SPECIALISTICO" and not _is_discipline_excluded(disc_text):
                    disc_set.add(disc_text)
            self._logger.stop_progress()

            # Notify callbacks with unique enti and discipline
            if on_enti_found and ente_set:
                on_enti_found(sorted(ente_set))
            if on_discipline_found and disc_set:
                on_discipline_found(sorted(disc_set))

            # Phase 2: Filter and download
            ente_filter_upper = ente_filter.strip().upper()
            disc_filter_upper = discipline_filter.strip().upper()

            # Pre-filter to identify matching rows and count
            rows_to_download: list[tuple[int, str, str, str]] = []
            for i, date_text, tipo_text, ente_text, disc_text in row_info:
                if selected_row_indices is not None and i not in selected_row_indices:
                    results.append(DocumentResult(
                        disciplina=tipo_text, skipped=True, download_path=None, error=None,
                        date_text=date_text,
                    ))
                    continue
                if not _is_tipologia_valida(tipo_text, allowed_types):
                    results.append(DocumentResult(
                        disciplina=tipo_text, skipped=True, download_path=None, error=None,
                        date_text=date_text,
                    ))
                    continue
                if ente_filter_upper and ente_filter_upper not in ente_text.upper():
                    results.append(DocumentResult(
                        disciplina=tipo_text, skipped=True, download_path=None, error=None,
                        date_text=date_text,
                    ))
                    continue
                # Discipline filter: applies only to REFERTO SPECIALISTICO rows
                if disc_filter_upper and tipo_text.upper() == "REFERTO SPECIALISTICO":
                    if disc_filter_upper not in disc_text.upper():
                        results.append(DocumentResult(
                            disciplina=tipo_text, skipped=True, download_path=None, error=None,
                            date_text=date_text,
                        ))
                        continue
                if date_from or date_to:
                    parsed = _parse_table_date(date_text)
                    if parsed:
                        if date_from and parsed < date_from:
                            results.append(DocumentResult(
                                disciplina=tipo_text, skipped=True, download_path=None, error=None,
                                date_text=date_text,
                            ))
                            continue
                        if date_to and parsed > date_to:
                            results.append(DocumentResult(
                                disciplina=tipo_text, skipped=True, download_path=None, error=None,
                                date_text=date_text,
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
                result = self._download_document(i, tipo_text, patient_name, visualizza_col, data_rows,
                                                 date_text=date_text)
                results.append(result)

        except Exception as e:
            self._logger.error(f"Errore lettura tabella per {codice_fiscale}: {e}")
            self._take_debug_screenshot(codice_fiscale)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        return results

    def _close_pdf_popup_tabs(self) -> None:
        """Close any popup tabs opened by Chrome's PDF viewer.

        The built-in PDF viewer renders embedded PNGs, which can cause
        'libpng error: Read Error' spam on Chrome's stderr.  Closing these
        tabs as early as possible silences the noise.
        """
        if not self._context:
            return
        for pg in self._context.pages:
            if pg == self._page:
                continue
            try:
                url = pg.url.lower()
                if "blob:" in url or url.endswith(".pdf"):
                    pg.close()
            except Exception:
                pass

    def _download_via_expect_response(self, clickable, save_path: Path, tipologia: str) -> bytes | None:
        """Download PDF using expect_response + fallback chain.

        Used in standard (non-CDP) mode where Playwright has full control
        of the browser and can intercept HTTP responses directly.
        """
        def _is_pdf_response(resp):
            ct = resp.headers.get("content-type", "")
            return "pdf" in ct or "octet-stream" in ct

        captured_downloads: list = []

        def _on_download(dl):
            captured_downloads.append(dl)

        self._page.on("download", _on_download)

        pdf_bytes = None
        self._logger.start_progress(f"Scaricamento PDF per {tipologia}")
        try:
            with self._page.expect_response(
                _is_pdf_response,
                timeout=self._config.download_timeout,
            ) as resp_info:
                clickable.click()
                try:
                    accetta = self._page.get_by_role(
                        "button", name="Accetta",
                    )
                    accetta.click(timeout=3000)
                except Exception:
                    pass  # no consent dialog — continue

            self._logger.stop_progress()
            response = resp_info.value
            self._logger.info(
                f"Risposta PDF: status {response.status}"
            )

            self._close_pdf_popup_tabs()

            try:
                pdf_bytes = response.body()
                if pdf_bytes and len(pdf_bytes) > 0:
                    self._logger.info(f"PDF letto via response: {len(pdf_bytes):,} bytes")
                else:
                    pdf_bytes = None
                    self._logger.debug("response.body() vuoto, provo fallback download")
            except Exception as body_err:
                self._logger.debug(
                    f"response.body() fallito ({body_err}), provo fallback download"
                )

        except Exception as resp_err:
            self._logger.stop_progress()
            self._logger.debug(f"expect_response fallito: {resp_err}")

        finally:
            self._page.remove_listener("download", _on_download)

        # ── Fallback 1: use browser download event ──
        if not pdf_bytes and captured_downloads:
            dl = captured_downloads[0]
            self._logger.info(
                f"Uso download browser (file: {dl.suggested_filename})"
            )
            dl.save_as(str(save_path))
            pdf_bytes = save_path.read_bytes()
            self._logger.info(f"PDF letto via download: {len(pdf_bytes):,} bytes")
            self._close_pdf_popup_tabs()

        # ── Fallback 2: read PDF from popup tab (inline viewer) ──
        if not pdf_bytes and self._context:
            for pg in self._context.pages:
                if pg == self._page:
                    continue
                try:
                    pg_url = pg.url.lower()
                    if "blob:" in pg_url or pg_url.endswith(".pdf"):
                        self._logger.info(f"Lettura PDF da tab popup: {pg_url[:100]}")
                        tab_resp = pg.goto(pg.url)
                        if tab_resp:
                            try:
                                pdf_bytes = tab_resp.body()
                                if pdf_bytes and len(pdf_bytes) > 0:
                                    self._logger.info(
                                        f"PDF letto da tab popup: {len(pdf_bytes):,} bytes"
                                    )
                            except Exception:
                                pass
                        break
                except Exception:
                    continue
            self._close_pdf_popup_tabs()

        return pdf_bytes

    def _download_via_expect_download(self, clickable, save_path: Path, tipologia: str) -> bytes | None:
        """Download PDF using expect_download (CDP mode).

        In CDP mode, Browser.setDownloadBehavior intercepts the HTTP response
        at the browser level, making it invisible to Playwright's network
        layer.  expect_response would block for the full timeout.  Instead,
        we use expect_download which hooks into the browser's download manager
        directly and resolves as soon as the file is saved.
        """
        pdf_bytes = None
        self._logger.start_progress(f"Scaricamento PDF per {tipologia}")
        try:
            with self._page.expect_download(
                timeout=self._config.download_timeout,
            ) as dl_info:
                clickable.click()
                try:
                    accetta = self._page.get_by_role(
                        "button", name="Accetta",
                    )
                    accetta.click(timeout=3000)
                except Exception:
                    pass  # no consent dialog — continue

            self._logger.stop_progress()
            dl = dl_info.value
            self._logger.info(
                f"Download CDP completato (file: {dl.suggested_filename})"
            )
            dl.save_as(str(save_path))
            pdf_bytes = save_path.read_bytes()
            self._logger.info(f"PDF letto via download CDP: {len(pdf_bytes):,} bytes")
            self._close_pdf_popup_tabs()

        except Exception as dl_err:
            self._logger.stop_progress()
            self._logger.debug(f"expect_download fallito: {dl_err}")

            # ── Fallback: PDF opened in popup tab instead of downloading ──
            if self._context:
                for pg in self._context.pages:
                    if pg == self._page:
                        continue
                    try:
                        pg_url = pg.url.lower()
                        if "blob:" in pg_url or pg_url.endswith(".pdf"):
                            self._logger.info(f"Lettura PDF da tab popup (CDP): {pg_url[:100]}")
                            tab_resp = pg.goto(pg.url)
                            if tab_resp:
                                try:
                                    pdf_bytes = tab_resp.body()
                                    if pdf_bytes and len(pdf_bytes) > 0:
                                        self._logger.info(
                                            f"PDF letto da tab popup: {len(pdf_bytes):,} bytes"
                                        )
                                except Exception:
                                    pass
                            break
                    except Exception:
                        continue
                self._close_pdf_popup_tabs()

        return pdf_bytes

    def _download_document(self, row_index: int, tipologia: str, patient_name: str,
                           visualizza_col: int | None, data_rows=None,
                           date_text: str = "") -> DocumentResult:
        for attempt in range(2):  # max 1 retry
            try:
                if data_rows is not None:
                    row = data_rows.nth(row_index)
                else:
                    row = self._page.locator("table tbody tr:has(td)").nth(row_index)

                if visualizza_col is not None:
                    visualizza_cell = row.locator("td").nth(visualizza_col)
                else:
                    visualizza_cell = row.locator("td").last

                cell_html = visualizza_cell.inner_html()
                self._logger.debug(f"HTML cella Visualizza: {cell_html[:200]}")

                clickable = visualizza_cell.locator(
                    "a, button, img, svg, i, span[class*='icon'], span[class*='glyph']"
                ).first
                if clickable.count() == 0:
                    clickable = visualizza_cell

                unique_name = (
                    f"{patient_name}_{row_index}.pdf".replace(" ", "_")
                )
                save_path = self._config.download_dir / unique_name

                # ── Download PDF: strategia dipende dalla modalità runtime ──
                if self._attached:
                    # CDP: expect_download è affidabile (Browser.setDownloadBehavior
                    # intercetta la risposta, rendendo expect_response inutilizzabile)
                    pdf_bytes = self._download_via_expect_download(clickable, save_path, tipologia)
                else:
                    # Standard: expect_response + catena di fallback
                    pdf_bytes = self._download_via_expect_response(clickable, save_path, tipologia)

                if not pdf_bytes:
                    raise RuntimeError(
                        "Nessun contenuto PDF ottenuto (ne' via rete ne' via download browser)"
                    )

                # ── Save captured bytes to disk ──
                if not save_path.exists() or save_path.stat().st_size == 0:
                    save_path.write_bytes(pdf_bytes)

                # ── Validate ──
                file_size = len(pdf_bytes)
                if file_size == 0:
                    save_path.unlink(missing_ok=True)
                    raise RuntimeError("File scaricato vuoto (0 bytes)")
                if pdf_bytes[:4] != b"%PDF":
                    self._logger.warning(
                        f"File non sembra un PDF "
                        f"(header: {pdf_bytes[:5]!r}, size: {file_size})"
                    )

                self._logger.info(
                    f"Download completato: {save_path.name} "
                    f"({file_size:,} bytes)"
                )
                return DocumentResult(
                    disciplina=tipologia, skipped=False,
                    download_path=save_path, error=None,
                    date_text=date_text,
                )

            except Exception as e:
                self._logger.stop_progress()
                if attempt == 0:
                    self._logger.warning(
                        f"Download fallito per riga {row_index + 1} "
                        f"({tipologia}), tentativo {attempt + 1}/2: {e}"
                    )
                    try:
                        self._close_pdf_popup_tabs()
                        self._page.reload(wait_until="networkidle")
                        self._wait_for_spinner()
                        self._page.wait_for_selector(
                            "table tbody tr", state="visible", timeout=15000,
                        )
                    except Exception:
                        pass
                else:
                    self._logger.error(
                        f"Download fallito definitivamente per riga "
                        f"{row_index + 1} ({tipologia}): {e}"
                    )
                    self._close_pdf_popup_tabs()
                    self._take_debug_screenshot(f"{patient_name}_row{row_index + 1}")

        return DocumentResult(
            disciplina=tipologia, skipped=False, download_path=None,
            error="Download fallito dopo retry",
            date_text=date_text,
        )

    def _take_debug_screenshot(self, label: str) -> None:
        try:
            screenshot_path = self._config.log_dir / f"debug_{label}.png"
            self._page.screenshot(path=str(screenshot_path))
            self._logger.debug(f"Screenshot debug salvato: {screenshot_path}")
        except Exception:
            pass
