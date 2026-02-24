"""Text anonymization and filtering for medical reports.

Removes all PII (patient name, fiscal code, dates of birth, addresses,
hospital headers, legal footers, administrative metadata) from extracted
PDF text, producing a clean report body safe for LLM processing.

Ported from MedicalReportMonitor C++ (TextParser.cpp).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .report_profiles import ProfileManager, ReportProfile

logger = logging.getLogger(__name__)


@dataclass
class AnonymizedReport:
    """Result of the anonymization pipeline."""

    patient_name: str       # Extracted patient name (for filename, NOT sent to LLM)
    anonymized_text: str    # Cleaned report body with PII removed
    profile_used: str       # Name of the profile that was applied
    success: bool
    error_message: str = ""


class TextAnonymizer:
    """Anonymizes Italian medical reports by removing PII via profile-based filtering."""

    @staticmethod
    def anonymize(raw_text: str, profile: ReportProfile | None = None) -> AnonymizedReport:
        """Run the full anonymization pipeline.

        Steps:
            1. Select the appropriate profile (or use the one provided).
            2. Extract the patient name (for filename use only).
            3. Filter lines: remove PII, headers, footers, admin metadata.
            4. Rejoin and normalize whitespace.
            5. Insert structural newlines where appropriate.

        Args:
            raw_text: Raw text extracted from the PDF.
            profile: Optional override profile. If None, auto-detected.

        Returns:
            AnonymizedReport with the cleaned text.
        """
        if not raw_text or not raw_text.strip():
            return AnonymizedReport(
                patient_name="",
                anonymized_text="",
                profile_used="",
                success=False,
                error_message="Testo vuoto",
            )

        # 1. Find profile
        if profile is None:
            profile = ProfileManager.find_profile(raw_text)

        # 2. Extract patient name
        patient_name = _extract_patient_name(raw_text, profile.patient_name_patterns)
        normalized_name = _normalize_filename(patient_name)

        # 3. Filter lines
        lines = raw_text.splitlines()
        kept_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if not _should_exclude_line(
                line, profile.exclude_patterns, profile.keep_patterns
            ):
                kept_lines.append(stripped)

        # 4. Join into single text
        body = " ".join(kept_lines)

        # 5. Insert newlines before structural patterns
        for pattern in profile.newline_before_patterns:
            try:
                body = re.sub(pattern, r"\n\g<0>", body, flags=re.IGNORECASE)
            except re.error:
                continue

        # 6. Normalize whitespace (preserve intentional newlines)
        body = _normalize_whitespace(body)

        return AnonymizedReport(
            patient_name=normalized_name,
            anonymized_text=body,
            profile_used=profile.name,
            success=True,
        )


def _extract_patient_name(text: str, patterns: list[str]) -> str:
    """Try each regex pattern to extract the patient name.

    Returns the first successful match or a fallback string.
    """
    for pattern in patterns:
        try:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and match.group(1):
                name = match.group(1).strip()
                if name:
                    return name
        except (re.error, IndexError):
            continue
    return "PAZIENTE_SCONOSCIUTO"


def _normalize_filename(name: str) -> str:
    """Convert a patient name into a safe filename fragment.

    'Rossi, Mario' -> 'ROSSI_MARIO'
    """
    result: list[str] = []
    last_was_sep = False
    for ch in name:
        if ch.isalpha():
            result.append(ch.upper())
            last_was_sep = False
        elif ch.isspace() or ch in (",", "."):
            if not last_was_sep and result:
                result.append("_")
                last_was_sep = True
    # Strip trailing separator
    text = "".join(result)
    return text.rstrip("_")


def _should_exclude_line(
    line: str,
    exclude_patterns: list[str],
    keep_patterns: list[str],
) -> bool:
    """Determine if a line should be excluded from the output.

    Keep patterns have HIGHER PRIORITY than exclude patterns:
    if a line matches a keep pattern, it is never excluded.
    """
    # Check keep patterns first (higher priority)
    for pattern in keep_patterns:
        try:
            if re.search(pattern, line, re.IGNORECASE):
                return False
        except re.error:
            continue

    # Then check exclude patterns
    for pattern in exclude_patterns:
        try:
            if re.search(pattern, line, re.IGNORECASE):
                return True
        except re.error:
            continue

    return False


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces, preserve intentional newlines, trim."""
    result: list[str] = []
    last_was_space = False
    for ch in text:
        if ch == "\n":
            # Remove trailing spaces before newline
            while result and result[-1] == " ":
                result.pop()
            result.append("\n")
            last_was_space = True
        elif ch in (" ", "\t", "\r"):
            if not last_was_space:
                result.append(" ")
                last_was_space = True
        else:
            result.append(ch)
            last_was_space = False

    normalized = "".join(result).strip()
    return normalized
