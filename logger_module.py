import json
import logging
from datetime import datetime
from pathlib import Path


class ProcessingLogger:
    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_file = log_dir / f"processing_{self._timestamp}.log"

        self._logger = logging.getLogger("fse_processor")
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()

        # Console handler
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        self._logger.addHandler(console)

        # File handler
        file_handler = logging.FileHandler(self._log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        self._logger.addHandler(file_handler)

        # Counters
        self.emails_found = 0
        self.emails_processed = 0
        self.emails_skipped = 0
        self.documents_downloaded = 0
        self.documents_renamed = 0
        self.documents_skipped = 0
        self.errors = 0

    def info(self, msg: str) -> None:
        self._logger.info(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def error(self, msg: str) -> None:
        self._logger.error(msg)
        self.errors += 1

    def debug(self, msg: str) -> None:
        self._logger.debug(msg)

    def save_summary(self) -> None:
        summary = {
            "timestamp": self._timestamp,
            "emails_found": self.emails_found,
            "emails_processed": self.emails_processed,
            "emails_skipped": self.emails_skipped,
            "documents_downloaded": self.documents_downloaded,
            "documents_renamed": self.documents_renamed,
            "documents_skipped": self.documents_skipped,
            "errors": self.errors,
        }
        summary_file = self._log_dir / f"summary_{self._timestamp}.json"
        summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        self.info(
            f"Riepilogo: {self.emails_processed}/{self.emails_found} email processate, "
            f"{self.documents_downloaded} documenti scaricati, "
            f"{self.documents_renamed} rinominati, "
            f"{self.documents_skipped} saltati, "
            f"{self.errors} errori"
        )
