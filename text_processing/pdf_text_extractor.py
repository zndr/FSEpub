"""PDF text extraction using pdfplumber.

Extracts selectable text from PDF files downloaded from the FSE portal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)


class PdfTextExtractor:
    """Extracts text content from medical report PDFs."""

    @staticmethod
    def extract(pdf_path: Path) -> str:
        """Extract full text from a PDF preserving layout.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text or empty string on failure.
        """
        try:
            texts: list[str] = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text(
                        x_tolerance=3,
                        y_tolerance=3,
                        layout=True,
                    )
                    if text:
                        texts.append(text)
            return "\n".join(texts)
        except Exception as e:
            logger.error("Errore estrazione testo da %s: %s", pdf_path.name, e)
            return ""

    @staticmethod
    def extract_simple(pdf_path: Path) -> str:
        """Extract text without layout preservation (denser, better for AI).

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Extracted text or empty string on failure.
        """
        try:
            texts: list[str] = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        texts.append(text)
            return "\n".join(texts)
        except Exception as e:
            logger.error("Errore estrazione testo da %s: %s", pdf_path.name, e)
            return ""

    @staticmethod
    def extract_zones(pdf_path: Path, profile_path: Path) -> str:
        """Extract text from specific zones defined in a JSON profile.

        The profile format matches MedicalReportMonitor's zone profiles::

            {
                "profile_name": "...",
                "zones": [
                    {"label": "...", "x": 0, "y": 0, "width": 595, "height": 842, "pages": 0}
                ]
            }

        Args:
            pdf_path: Path to the PDF file.
            profile_path: Path to the JSON zone profile.

        Returns:
            Extracted text from all zones, concatenated.
        """
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        except Exception as e:
            logger.error("Errore caricamento profilo zone %s: %s", profile_path, e)
            return ""

        zones = profile.get("zones", [])
        if not zones:
            logger.warning("Nessuna zona definita nel profilo %s", profile_path)
            return ""

        try:
            results: list[str] = []
            with pdfplumber.open(pdf_path) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    for zone in zones:
                        if not _zone_applies_to_page(zone, page_idx):
                            continue
                        bbox = (
                            zone["x"],
                            zone["y"],
                            zone["x"] + zone["width"],
                            zone["y"] + zone["height"],
                        )
                        cropped = page.within_bbox(bbox)
                        text = cropped.extract_text()
                        if text and text.strip():
                            results.append(text.strip())
            return "\n".join(results)
        except Exception as e:
            logger.error("Errore estrazione zone da %s: %s", pdf_path.name, e)
            return ""

    @staticmethod
    def has_selectable_text(pdf_path: Path) -> bool:
        """Check if a PDF contains selectable (non-scanned) text.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            True if at least 50 characters of text were found.
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text and len(text.strip()) > 50:
                        return True
            return False
        except Exception:
            return False

    @staticmethod
    def get_page_count(pdf_path: Path) -> int:
        """Return the number of pages in a PDF.

        Returns:
            Page count, or -1 on error.
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                return len(pdf.pages)
        except Exception:
            return -1


def _zone_applies_to_page(zone: dict, page_idx: int) -> bool:
    """Check if a zone definition applies to a given 0-indexed page."""
    pages = zone.get("pages", "current")
    if pages == "all":
        return True
    if isinstance(pages, list):
        return page_idx in pages
    if isinstance(pages, int):
        return page_idx == pages
    return False
