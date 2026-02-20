import imaplib
import re
from dataclasses import dataclass
from email import message_from_bytes
from email.header import decode_header

from config import Config
from logger_module import ProcessingLogger


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
        self._connection: imaplib.IMAP4_SSL | imaplib.IMAP4 | None = None

    def connect(self) -> None:
        self._logger.info(f"Connessione IMAP a {self._config.imap_host}:{self._config.imap_port}")
        if self._config.imap_use_ssl:
            self._connection = imaplib.IMAP4_SSL(self._config.imap_host, self._config.imap_port)
        else:
            self._connection = imaplib.IMAP4(self._config.imap_host, self._config.imap_port)
        self._connection.login(self._config.email_user, self._config.email_pass)
        self._logger.info("Login IMAP riuscito")

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

    def fetch_unread_emails(self) -> list[EmailData]:
        if not self._connection:
            raise RuntimeError("Non connesso al server IMAP")

        self._connection.select("INBOX")
        search_criteria = '(UNSEEN FROM "Mail CRS Lombardia" SUBJECT "Nuovo Documento per")'
        status, data = self._connection.uid("search", None, search_criteria)

        if status != "OK" or not data[0]:
            self._logger.info("Nessuna email non letta trovata")
            return []

        uids = data[0].split()
        self._logger.info(f"Trovate {len(uids)} email non lette")

        emails: list[EmailData] = []
        for uid_bytes in uids:
            uid = uid_bytes.decode()
            email_data = self._fetch_and_parse(uid)
            if email_data:
                emails.append(email_data)
            else:
                self._logger.warning(f"Email UID {uid}: parsing fallito, sarà ritentata al prossimo run")

        return emails

    def mark_as_read(self, uid: str) -> None:
        if not self._connection:
            raise RuntimeError("Non connesso al server IMAP")
        self._connection.uid("store", uid, "+FLAGS", "\\Seen")
        self._logger.debug(f"Email UID {uid} marcata come letta")

    def mark_all_matching_as_unread(self) -> int:
        """Mark all matching SEEN emails as UNSEEN for re-processing."""
        if not self._connection:
            raise RuntimeError("Non connesso al server IMAP")
        self._connection.select("INBOX")
        search_criteria = '(SEEN FROM "Mail CRS Lombardia" SUBJECT "Nuovo Documento per")'
        status, data = self._connection.uid("search", None, search_criteria)
        if status != "OK" or not data[0]:
            return 0
        uids = data[0].split()
        for uid_bytes in uids:
            uid = uid_bytes.decode()
            self._connection.uid("store", uid, "-FLAGS", "\\Seen")
        return len(uids)

    def _fetch_and_parse(self, uid: str) -> EmailData | None:
        status, data = self._connection.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK" or not data[0]:
            return None

        msg = message_from_bytes(data[0][1])

        # Decode subject
        subject = self._decode_subject(msg.get("Subject", ""))
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
    def _decode_subject(raw_subject: str) -> str:
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
