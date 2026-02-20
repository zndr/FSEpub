"""GUI tkinter per FSE Processor."""

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from config import Config
from email_client import EmailClient
from logger_module import ProcessingLogger
from main import run_processing

ENV_FILE = "settings.env"

# Ordered list of settings: (env_key, label, default, kind)
# kind: "text", "password", "dir", "exe", "bool", "int"
SETTINGS_SPEC = [
    ("EMAIL_USER", "Email utente", "", "text"),
    ("EMAIL_PASS", "Email password", "", "password"),
    ("IMAP_HOST", "IMAP Host", "mail-crs-lombardia.fastweb360.it", "text"),
    ("IMAP_PORT", "IMAP Port", "993", "int"),
    ("DOWNLOAD_DIR", "Directory download", "./downloads", "dir"),
    ("PDF_READER", "Lettore PDF (.exe)", r"C:\Program Files\SumatraPDF\SumatraPDF.exe", "exe"),
    ("HEADLESS", "Headless browser", "false", "bool"),
    ("DOWNLOAD_TIMEOUT", "Download timeout (sec)", "60", "int"),
    ("PAGE_TIMEOUT", "Page timeout (sec)", "30", "int"),
]


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
        self.title("FSE Processor")
        self.geometry("720x700")
        self.resizable(True, True)

        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._fields: dict[str, tk.Variable] = {}

        self._build_ui()
        self._load_settings()

    # ---- UI construction ----

    def _build_ui(self) -> None:
        # Settings frame
        settings_frame = tk.LabelFrame(self, text="Impostazioni", padx=8, pady=8)
        settings_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        for row_idx, (key, label, default, kind) in enumerate(SETTINGS_SPEC):
            tk.Label(settings_frame, text=label, anchor="w").grid(
                row=row_idx, column=0, sticky="w", padx=(0, 8), pady=2,
            )

            if kind == "bool":
                var = tk.BooleanVar(value=default.lower() == "true")
                cb = tk.Checkbutton(settings_frame, variable=var)
                cb.grid(row=row_idx, column=1, sticky="w", pady=2)
                self._fields[key] = var
            else:
                var = tk.StringVar(value=default)
                show = "*" if kind == "password" else ""
                entry = tk.Entry(settings_frame, textvariable=var, width=52, show=show)
                entry.grid(row=row_idx, column=1, sticky="ew", pady=2)
                self._fields[key] = var

                if kind == "dir":
                    tk.Button(
                        settings_frame, text="Sfoglia...",
                        command=lambda v=var: self._browse_dir(v),
                    ).grid(row=row_idx, column=2, padx=(4, 0), pady=2)
                elif kind == "exe":
                    tk.Button(
                        settings_frame, text="Sfoglia...",
                        command=lambda v=var: self._browse_exe(v),
                    ).grid(row=row_idx, column=2, padx=(4, 0), pady=2)

        settings_frame.columnconfigure(1, weight=1)

        tk.Button(settings_frame, text="Salva Impostazioni", command=self._save_settings).grid(
            row=len(SETTINGS_SPEC), column=0, columnspan=3, pady=(8, 0),
        )

        # Controls frame
        ctrl_frame = tk.LabelFrame(self, text="Controlli", padx=8, pady=8)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=5)

        self._btn_check = tk.Button(ctrl_frame, text="Controlla Email", command=self._check_email)
        self._btn_check.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_start = tk.Button(ctrl_frame, text="Avvia", command=self._start_processing)
        self._btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_stop = tk.Button(ctrl_frame, text="Interrompi", command=self._stop_processing, state=tk.DISABLED)
        self._btn_stop.pack(side=tk.LEFT)

        # Console frame
        console_frame = tk.LabelFrame(self, text="Console", padx=8, pady=8)
        console_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        self._console = ScrolledText(console_frame, state=tk.DISABLED, wrap=tk.WORD, height=16)
        self._console.pack(fill=tk.BOTH, expand=True)

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
            msg = f"{count} email con referti da scaricare"
            self.after(0, self._log, msg)
            self.after(0, lambda: messagebox.showinfo("Conteggio Email", msg))
        except Exception as e:
            self.after(0, self._log, f"Errore: {e}")
            self.after(0, lambda: messagebox.showerror("Errore", str(e)))
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

            run_processing(config, logger, self._stop_event)
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


if __name__ == "__main__":
    app = FSEApp()
    app.mainloop()
