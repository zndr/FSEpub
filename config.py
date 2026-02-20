import os
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv

from app_paths import paths


@dataclass
class Config:
    # POP3
    email_user: str
    email_pass: str
    pop3_host: str
    pop3_port: int
    pop3_use_ssl: bool

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

    @classmethod
    def load(cls, env_path: str | None = None) -> "Config":
        env_file = Path(env_path) if env_path else paths.settings_file
        if not env_file.exists():
            raise FileNotFoundError(f"File di configurazione non trovato: {env_file}")
        load_dotenv(env_file)

        email_user = os.getenv("EMAIL_USER", "")
        email_pass = os.getenv("EMAIL_PASS", "")
        pop3_host = os.getenv("IMAP_HOST", "mail-crs-lombardia.fastweb360.it")

        if not email_user or not email_pass:
            raise ValueError("EMAIL_USER e EMAIL_PASS sono obbligatori in settings.env")

        pop3_port = int(os.getenv("IMAP_PORT", "995"))
        pop3_use_ssl = os.getenv("IMAP_USE_SSL", "true").lower() == "true"

        download_dir = Path(os.getenv("DOWNLOAD_DIR", str(paths.default_download_dir)))
        log_dir = Path(os.getenv("LOG_DIR", str(paths.log_dir)))
        browser_data_dir = Path(os.getenv("BROWSER_DATA_DIR", str(paths.browser_data_dir)))

        headless = os.getenv("HEADLESS", "false").lower() == "true"
        download_timeout = int(os.getenv("DOWNLOAD_TIMEOUT", "60")) * 1000
        page_timeout = int(os.getenv("PAGE_TIMEOUT", "30")) * 1000
        pdf_reader = os.getenv("PDF_READER", "default")
        browser_channel = os.getenv("BROWSER_CHANNEL", "msedge")

        config = cls(
            email_user=email_user,
            email_pass=email_pass,
            pop3_host=pop3_host,
            pop3_port=pop3_port,
            pop3_use_ssl=pop3_use_ssl,
            download_dir=download_dir,
            log_dir=log_dir,
            browser_data_dir=browser_data_dir,
            headless=headless,
            download_timeout=download_timeout,
            page_timeout=page_timeout,
            pdf_reader=pdf_reader,
            browser_channel=browser_channel,
        )
        config._create_directories()
        return config

    def _create_directories(self) -> None:
        for d in (self.download_dir, self.log_dir, self.browser_data_dir):
            d.mkdir(parents=True, exist_ok=True)
