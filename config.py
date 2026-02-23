import os
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv

from app_paths import paths
from credential_manager import decrypt_password


@dataclass
class Config:
    # IMAP
    email_user: str
    email_pass: str
    imap_host: str
    imap_port: int
    imap_use_ssl: bool
    imap_folders: list[str]

    # Paths
    download_dir: Path
    log_dir: Path
    browser_data_dir: Path

    # Processing
    headless: bool
    download_timeout: int  # milliseconds (for Playwright)
    page_timeout: int      # milliseconds (for Playwright)
    pdf_reader: str
    browser_channel: str
    delete_after_processing: bool
    mark_as_read: bool
    use_existing_browser: bool
    cdp_port: int
    max_emails: int  # 0 = unlimited
    move_dir: Path | None
    process_text: bool
    text_dir: Path | None

    @classmethod
    def load(cls, env_path: str | None = None) -> "Config":
        env_file = Path(env_path) if env_path else paths.settings_file
        if not env_file.exists():
            raise FileNotFoundError(f"File di configurazione non trovato: {env_file}")
        load_dotenv(env_file, override=True)

        email_user = os.getenv("EMAIL_USER", "")
        email_pass = decrypt_password(os.getenv("EMAIL_PASS", ""))
        imap_host = os.getenv("IMAP_HOST", "mail-crs-lombardia.fastweb360.it")

        if not email_user or not email_pass:
            raise ValueError("EMAIL_USER e EMAIL_PASS sono obbligatori in settings.env")

        imap_port = int(os.getenv("IMAP_PORT", "993"))
        imap_use_ssl = os.getenv("IMAP_USE_SSL", "true").lower() == "true"
        imap_folders_raw = os.getenv("IMAP_FOLDER", "INBOX")
        imap_folders = [f.strip() for f in imap_folders_raw.split(",") if f.strip()]

        download_dir = Path(os.getenv("DOWNLOAD_DIR", str(paths.default_download_dir)))
        log_dir = Path(os.getenv("LOG_DIR", str(paths.log_dir)))
        browser_data_dir = Path(os.getenv("BROWSER_DATA_DIR", str(paths.browser_data_dir)))

        headless = os.getenv("HEADLESS", "false").lower() == "true"
        download_timeout = int(os.getenv("DOWNLOAD_TIMEOUT", "120")) * 1000
        page_timeout = int(os.getenv("PAGE_TIMEOUT", "60")) * 1000
        pdf_reader = os.getenv("PDF_READER", "default")
        browser_channel = os.getenv("BROWSER_CHANNEL", "msedge")
        delete_after_processing = os.getenv("DELETE_AFTER_PROCESSING", "false").lower() == "true"
        mark_as_read = os.getenv("MARK_AS_READ", "true").lower() == "true"
        use_existing_browser = os.getenv("USE_EXISTING_BROWSER", "true").lower() == "true"
        cdp_port = int(os.getenv("CDP_PORT", "9222"))
        max_emails = int(os.getenv("MAX_EMAILS", "0"))
        move_dir_str = os.getenv("MOVE_DIR", "")
        move_dir = Path(move_dir_str) if move_dir_str else None
        process_text = os.getenv("PROCESS_TEXT", "false").lower() == "true"
        text_dir_str = os.getenv("TEXT_DIR", "")
        text_dir = Path(text_dir_str) if text_dir_str else None

        config = cls(
            email_user=email_user,
            email_pass=email_pass,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_use_ssl=imap_use_ssl,
            imap_folders=imap_folders,
            download_dir=download_dir,
            log_dir=log_dir,
            browser_data_dir=browser_data_dir,
            headless=headless,
            download_timeout=download_timeout,
            page_timeout=page_timeout,
            pdf_reader=pdf_reader,
            browser_channel=browser_channel,
            delete_after_processing=delete_after_processing,
            mark_as_read=mark_as_read,
            use_existing_browser=use_existing_browser,
            cdp_port=cdp_port,
            max_emails=max_emails,
            move_dir=move_dir,
            process_text=process_text,
            text_dir=text_dir,
        )
        config._create_directories()
        return config

    def _create_directories(self) -> None:
        for d in (self.download_dir, self.log_dir, self.browser_data_dir):
            d.mkdir(parents=True, exist_ok=True)
