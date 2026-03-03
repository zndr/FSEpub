"""Data structures for processing session results."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FailedDownload:
    """Single download failure with context."""
    patient_name: str
    codice_fiscale: str
    disciplina: str
    date_text: str
    error: str


@dataclass
class ProcessingSummary:
    """Summary of a processing session."""
    downloaded: int = 0
    skipped: int = 0
    errors: int = 0
    emails_found: int = 0
    emails_processed: int = 0
    failures: list[FailedDownload] = field(default_factory=list)
    report_path: Path | None = None
