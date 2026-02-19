import os
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Config:
    # IMAP
    email_user: str
    email_pass: str
    imap_host: str
    imap_port: int
    imap_use_ssl: bool

    # Paths
    download_dir: Path
    log_dir: Path
    browser_data_dir: Path

    # Processing
    headless: bool
    download_timeout: int  # milliseconds (for Playwright)
    page_timeout: int      # milliseconds (for Playwright)

    @classmethod
    def load(cls, env_path: str = "settings.env") -> "Config":
        env_file = Path(env_path)
        if not env_file.exists():
            raise FileNotFoundError(f"File di configurazione non trovato: {env_path}")
        load_dotenv(env_file)

        email_user = os.getenv("EMAIL_USER", "")
        email_pass = os.getenv("EMAIL_PASS", "")
        imap_host = os.getenv("IMAP_HOST", "mail-crs-lombardia.fastweb360.it")

        if not email_user or not email_pass:
            raise ValueError("EMAIL_USER e EMAIL_PASS sono obbligatori in settings.env")

        imap_port = int(os.getenv("IMAP_PORT", "993"))
        imap_use_ssl = os.getenv("IMAP_USE_SSL", "true").lower() == "true"

        download_dir = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
        log_dir = Path(os.getenv("LOG_DIR", "./logs"))
        browser_data_dir = Path(os.getenv("BROWSER_DATA_DIR", "./browser_data"))

        headless = os.getenv("HEADLESS", "false").lower() == "true"
        download_timeout = int(os.getenv("DOWNLOAD_TIMEOUT", "60")) * 1000
        page_timeout = int(os.getenv("PAGE_TIMEOUT", "30")) * 1000

        config = cls(
            email_user=email_user,
            email_pass=email_pass,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_use_ssl=imap_use_ssl,
            download_dir=download_dir,
            log_dir=log_dir,
            browser_data_dir=browser_data_dir,
            headless=headless,
            download_timeout=download_timeout,
            page_timeout=page_timeout,
        )
        config._create_directories()
        return config

    def _create_directories(self) -> None:
        for d in (self.download_dir, self.log_dir, self.browser_data_dir):
            d.mkdir(parents=True, exist_ok=True)
