import json
import poplib
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

# Persistent file tracking processed UIDs (POP3 has no server-side read flags)
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


@dataclass
class EmailData:
    uid: str
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
        self._connection: poplib.POP3_SSL | poplib.POP3 | None = None

    def connect(self) -> None:
        host = self._config.pop3_host
        port = self._config.pop3_port
        self._logger.info(f"Connessione POP3 a {host}:{port}")
        try:
            if self._config.pop3_use_ssl:
                ctx = ssl.create_default_context()
                try:
                    self._connection = poplib.POP3_SSL(host, port, context=ctx)
                except ssl.SSLCertVerificationError:
                    self._logger.warning(
                        "Certificato SSL del server non valido, tentativo senza verifica certificato..."
                    )
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    self._connection = poplib.POP3_SSL(host, port, context=ctx)
            else:
                self._connection = poplib.POP3(host, port)
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
        except poplib.error_proto as e:
            msg = str(e)
            raise ConnectionError(f"Errore POP3 connettendo a {host}:{port}: {msg}")
        except Exception as e:
            raise ConnectionError(
                f"Errore imprevisto connettendo a {host}:{port}: {type(e).__name__}: {e}"
            )

        try:
            self._connection.user(self._config.email_user)
            self._connection.pass_(self._config.email_pass)
        except poplib.error_proto as e:
            raise ConnectionError(f"Login POP3 fallito: {e}")

        self._logger.info("Login POP3 riuscito")

    def disconnect(self) -> None:
        if self._connection:
            try:
                self._connection.quit()
            except Exception:
                pass
            self._connection = None

    def fetch_unread_emails(self) -> list[EmailData]:
        if not self._connection:
            raise RuntimeError("Non connesso al server POP3")

        # Get persistent UIDs for all messages on the server
        resp, uidl_list, _ = self._connection.uidl()
        # uidl_list: [b"1 <uid>", b"2 <uid>", ...]
        server_msgs: list[tuple[int, str]] = []
        for item in uidl_list:
            parts = item.decode().split(None, 1)
            if len(parts) == 2:
                msg_num = int(parts[0])
                uid = parts[1]
                server_msgs.append((msg_num, uid))

        # Load or seed the processed UIDs tracking file
        processed = _load_processed_uids()
        all_uids = {uid for _, uid in server_msgs}

        if not _PROCESSED_UIDS_FILE.exists():
            # First run after migration: seed all current UIDs as already processed.
            # POP3 has no read/unread flags, so we mark everything on the server
            # as "already seen". Only emails arriving AFTER this point will be detected.
            _save_processed_uids(all_uids)
            self._logger.info(
                f"Primo avvio POP3: {len(all_uids)} email esistenti marcate come già processate. "
                f"Da ora in poi verranno rilevate solo le nuove email."
            )
            return []

        # Filter out already-processed UIDs
        new_msgs = [(num, uid) for num, uid in server_msgs if uid not in processed]

        if not new_msgs:
            self._logger.info("Nessuna email non processata trovata")
            return []

        self._logger.info(f"Trovati {len(new_msgs)} messaggi non ancora processati, analisi in corso...")

        emails: list[EmailData] = []
        for msg_num, uid in new_msgs:
            email_data = self._fetch_and_parse(msg_num, uid)
            if email_data:
                emails.append(email_data)

        self._logger.info(f"Trovate {len(emails)} email con referti FSE")
        return emails

    def mark_as_read(self, uid: str) -> None:
        """Mark a message as processed by adding its UID to the local tracking file."""
        processed = _load_processed_uids()
        processed.add(uid)
        _save_processed_uids(processed)
        self._logger.debug(f"Email UID {uid} marcata come processata")

    def _fetch_and_parse(self, msg_num: int, uid: str) -> EmailData | None:
        try:
            resp, lines, octets = self._connection.retr(msg_num)
        except poplib.error_proto:
            return None

        raw_email = b"\r\n".join(lines)
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
