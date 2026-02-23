import imaplib
import json
import re
import socket
import ssl
from dataclasses import dataclass
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path

from app_paths import paths
from config import Config
from logger_module import ProcessingLogger

# Persistent file tracking processed UIDs (backup for IMAP \Seen flag)
_PROCESSED_UIDS_FILE: Path = paths._data_dir / "processed_uids.json"


def _load_processed_uids() -> set[str]:
    """Load the set of already-processed UIDs from disk."""
    if not _PROCESSED_UIDS_FILE.exists():
        return set()
    try:
        data = json.loads(_PROCESSED_UIDS_FILE.read_text(encoding="utf-8"))
        return set(data.get("uids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_processed_uids(uids: set[str]) -> None:
    """Persist the set of processed UIDs to disk."""
    _PROCESSED_UIDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROCESSED_UIDS_FILE.write_text(
        json.dumps({"uids": sorted(uids)}), encoding="utf-8",
    )


def _uid_key(folder: str, uid: str) -> str:
    """Composite key for local tracking: 'folder:uid'."""
    return f"{folder}:{uid}"


@dataclass
class EmailData:
    uid: str
    folder: str
    patient_name: str
    fse_link: str
    codice_fiscale: str
    raw_subject: str


class EmailClient:
    SUBJECT_PATTERN = re.compile(r"Nuovo Documento per\s+(.+?)\s+nato", re.IGNORECASE)
    FSE_URL_PATTERN = re.compile(
        r"https://operatorisiss\.servizirl\.it/opefseie/#/\?codiceFiscale=([A-Z0-9]{16})"
    )

    def __init__(self, config: Config, logger: ProcessingLogger) -> None:
        self._config = config
        self._logger = logger
        self._connection: imaplib.IMAP4_SSL | imaplib.IMAP4 | None = None
        self.limit_reached: bool = False

    def connect(self) -> None:
        host = self._config.imap_host
        port = self._config.imap_port
        self._logger.info(f"Connessione IMAP a {host}:{port}")
        try:
            if self._config.imap_use_ssl:
                ctx = ssl.create_default_context()
                try:
                    self._connection = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
                except ssl.SSLCertVerificationError:
                    self._logger.warning(
                        "Certificato SSL del server non valido, tentativo senza verifica certificato..."
                    )
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    self._connection = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
            else:
                self._connection = imaplib.IMAP4(host, port)
        except socket.gaierror:
            raise ConnectionError(
                f"Impossibile risolvere il server '{host}'. Controlla il nome host."
            )
        except socket.timeout:
            raise ConnectionError(
                f"Timeout di connessione a {host}:{port}. Controlla host e porta."
            )
        except ConnectionRefusedError:
            raise ConnectionError(
                f"Connessione rifiutata da {host}:{port}. Controlla host e porta."
            )
        except ssl.SSLError as e:
            raise ConnectionError(
                f"Errore SSL connettendo a {host}:{port}: {e.reason or e}"
            )
        except OSError as e:
            raise ConnectionError(
                f"Errore di rete connettendo a {host}:{port}: {e.strerror or e}"
            )
        except imaplib.IMAP4.error as e:
            msg = str(e)
            raise ConnectionError(f"Errore IMAP connettendo a {host}:{port}: {msg}")
        except Exception as e:
            raise ConnectionError(
                f"Errore imprevisto connettendo a {host}:{port}: {type(e).__name__}: {e}"
            )

        try:
            self._connection.login(self._config.email_user, self._config.email_pass)
        except imaplib.IMAP4.error as e:
            raise ConnectionError(f"Login IMAP fallito: {e}")

        folders = self._config.imap_folders or ["INBOX"]
        self._logger.info(f"Login IMAP riuscito (cartelle: {', '.join(folders)})")

    def disconnect(self) -> None:
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            try:
                self._connection.logout()
            except Exception:
                pass
            self._connection = None

    def _select_folder(self, folder: str) -> bool:
        """Select a mailbox folder. Returns True on success."""
        try:
            status, _ = self._connection.select(folder)
            return status == "OK"
        except imaplib.IMAP4.error as e:
            self._logger.warning(f"Impossibile selezionare cartella '{folder}': {e}")
            return False

    def fetch_unread_emails(self) -> list[EmailData]:
        if not self._connection:
            raise RuntimeError("Non connesso al server IMAP")

        folders = self._config.imap_folders or ["INBOX"]
        emails: list[EmailData] = []
        self.limit_reached = False
        max_emails = self._config.max_emails

        for folder in folders:
            if max_emails > 0 and len(emails) >= max_emails:
                self.limit_reached = True
                break

            if not self._select_folder(folder):
                continue

            self._logger.info(f"Ricerca in '{folder}'...")

            # Search for unseen messages
            status, data = self._connection.uid("SEARCH", None, "UNSEEN")
            if status != "OK":
                self._logger.warning(f"SEARCH UNSEEN fallita in '{folder}': {status}")
                continue

            uid_list = data[0].split() if data[0] else []
            use_local_filter = False
            if not uid_list:
                # Fallback: search ALL and filter by local processed_uids
                self._logger.info(f"Nessun messaggio UNSEEN in '{folder}', controllo con tracking locale...")
                status, data = self._connection.uid("SEARCH", None, "ALL")
                if status != "OK" or not data[0]:
                    continue
                uid_list = data[0].split()
                use_local_filter = True

            if use_local_filter:
                processed = _load_processed_uids()
                new_uids = [
                    uid for uid in uid_list
                    if _uid_key(folder, uid.decode()) not in processed
                    and uid.decode() not in processed  # backward compat with old plain UIDs
                ]
            else:
                new_uids = list(uid_list)

            if not new_uids:
                continue

            self._logger.info(f"Trovati {len(new_uids)} messaggi da analizzare in '{folder}'...")

            for uid_bytes in new_uids:
                uid = uid_bytes.decode()
                email_data = self._fetch_and_parse(uid, folder)
                if email_data:
                    emails.append(email_data)
                    if max_emails > 0 and len(emails) >= max_emails:
                        self.limit_reached = True
                        self._logger.info(
                            f"Raggiunto limite di {max_emails} email FSE, "
                            f"le restanti saranno processate al prossimo avvio"
                        )
                        break

        self._logger.info(f"Trovate {len(emails)} email con referti FSE")
        return emails

    def _ensure_connected(self) -> None:
        """Verify the IMAP connection is alive; reconnect if it dropped."""
        if not self._connection:
            self._logger.info("Connessione IMAP assente, riconnessione...")
            self.connect()
            return
        try:
            self._connection.noop()
        except Exception:
            self._logger.info("Connessione IMAP scaduta, riconnessione...")
            self._connection = None
            self.connect()

    def mark_as_read(self, uid: str, folder: str) -> None:
        """Mark a message as read on the server (+FLAGS \\Seen) and in local tracking."""
        self._ensure_connected()
        if self._connection:
            try:
                self._select_folder(folder)
                self._connection.uid("STORE", uid, "+FLAGS", "\\Seen")
            except Exception as e:
                self._logger.warning(f"Impossibile impostare flag \\Seen per UID {uid}: {e}")
        # Also track locally as backup
        processed = _load_processed_uids()
        processed.add(_uid_key(folder, uid))
        _save_processed_uids(processed)
        self._logger.debug(f"Email UID {uid} marcata come letta")

    def track_uid_locally(self, uid: str, folder: str) -> None:
        """Track a UID as processed locally without setting IMAP flags."""
        processed = _load_processed_uids()
        processed.add(_uid_key(folder, uid))
        _save_processed_uids(processed)

    def delete_message(self, uid: str, folder: str) -> None:
        """Mark a message for deletion on the server and expunge."""
        self._ensure_connected()
        if not self._connection:
            raise RuntimeError("Non connesso al server IMAP")
        try:
            self._select_folder(folder)
            self._connection.uid("STORE", uid, "+FLAGS", "\\Deleted")
            self._connection.expunge()
            self._logger.info(f"Email UID {uid} eliminata dal server")
        except imaplib.IMAP4.error as e:
            self._logger.warning(f"Impossibile eliminare UID {uid}: {e}")

    def _fetch_and_parse(self, uid: str, folder: str) -> EmailData | None:
        try:
            status, data = self._connection.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not data or data[0] is None:
                self._logger.debug(f"BODY.PEEK[] fallito per UID {uid}, uso RFC822")
                status, data = self._connection.uid("FETCH", uid, "(RFC822)")
                if status != "OK" or not data or data[0] is None:
                    return None
        except imaplib.IMAP4.error:
            try:
                self._logger.debug(f"BODY.PEEK[] non supportato, uso RFC822 per UID {uid}")
                status, data = self._connection.uid("FETCH", uid, "(RFC822)")
                if status != "OK" or not data or data[0] is None:
                    return None
            except imaplib.IMAP4.error as e:
                self._logger.warning(f"Impossibile recuperare email UID {uid}: {e}")
                return None

        raw_email = data[0][1]
        msg = message_from_bytes(raw_email)

        # Filter by sender (decode MIME-encoded From header)
        raw_from = msg.get("From", "")
        sender = self._decode_header(raw_from)
        if "mail crs lombardia" not in sender.lower():
            self._logger.debug(f"Email UID {uid}: scartata, From={sender!r}")
            return None

        # Decode and filter by subject
        subject = self._decode_header(msg.get("Subject", ""))
        if "nuovo documento per" not in subject.lower():
            self._logger.debug(f"Email UID {uid}: scartata, Subject={subject!r}")
            return None

        self._logger.debug(f"Email UID {uid}: subject = {subject}")

        # Extract patient name from subject
        name_match = self.SUBJECT_PATTERN.search(subject)
        patient_name = name_match.group(1).strip() if name_match else "UNKNOWN"

        # Extract FSE link and codice fiscale from body
        fse_link, codice_fiscale = self._extract_fse_data(msg)
        if not fse_link:
            self._logger.warning(f"Email UID {uid}: nessun link FSE trovato nel corpo")
            return None

        return EmailData(
            uid=uid,
            folder=folder,
            patient_name=patient_name,
            fse_link=fse_link,
            codice_fiscale=codice_fiscale,
            raw_subject=subject,
        )

    @staticmethod
    def _decode_header(raw_subject: str) -> str:
        parts = decode_header(raw_subject)
        decoded = []
        for content, charset in parts:
            if isinstance(content, bytes):
                decoded.append(content.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(content)
        return " ".join(decoded)

    def _extract_fse_data(self, msg) -> tuple[str, str]:
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                body = payload.decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
                url_match = self.FSE_URL_PATTERN.search(body)
                if url_match:
                    return url_match.group(0), url_match.group(1)
        return "", ""
