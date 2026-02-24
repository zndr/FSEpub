"""Text processing orchestrator.

Coordinates PDF extraction, anonymization, and (optionally) LLM analysis
into a single pipeline that integrates with the FSE download workflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .pdf_text_extractor import PdfTextExtractor
from .text_anonymizer import AnonymizedReport, TextAnonymizer
from .llm_analyzer import LLMAnalyzer, LLMConfig

logger = logging.getLogger(__name__)


class ProcessingMode(Enum):
    """How to process extracted text."""

    AI_ASSISTED = "ai"       # Anonymize -> send to LLM -> structured output
    LOCAL_ONLY = "local"     # Anonymize -> regex filtering -> clean output
    DISABLED = "disabled"    # No text processing


@dataclass
class ProcessingResult:
    """Outcome of the text processing pipeline."""

    success: bool
    pdf_path: Path
    output_text: str            # Final processed text
    anonymized_text: str        # Intermediate anonymized text (for diagnostics)
    patient_name: str           # Extracted patient name
    profile_used: str           # Report profile that matched
    mode: ProcessingMode        # Which mode was used
    ai_used: bool = False       # Whether AI analysis was actually applied
    error_message: str = ""


class TextProcessor:
    """Orchestrates the text processing pipeline.

    Usage::

        processor = TextProcessor(ProcessingMode.LOCAL_ONLY)
        result = processor.process(pdf_path)
        if result.success:
            processor.save_result(result, output_dir, "RSSMRA80_ROSSI_LAB")
    """

    def __init__(
        self,
        mode: ProcessingMode = ProcessingMode.LOCAL_ONLY,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.mode = mode
        self.llm_config = llm_config

    def process(self, pdf_path: Path) -> ProcessingResult:
        """Run the full text processing pipeline on a PDF.

        Steps:
            1. Extract raw text from the PDF.
            2. Anonymize (remove PII, headers, footers).
            3. If AI mode: send anonymized text to LLM (future).
            4. Return processed result.

        Args:
            pdf_path: Path to the downloaded PDF.

        Returns:
            ProcessingResult with the extracted and processed text.
        """
        # Step 1: Extract text
        raw_text = PdfTextExtractor.extract_simple(pdf_path)
        if not raw_text.strip():
            # Retry with layout mode
            raw_text = PdfTextExtractor.extract(pdf_path)

        if not raw_text.strip():
            return ProcessingResult(
                success=False,
                pdf_path=pdf_path,
                output_text="",
                anonymized_text="",
                patient_name="",
                profile_used="",
                mode=self.mode,
                error_message=f"Nessun testo estraibile da {pdf_path.name}",
            )

        # Step 2: Anonymize
        anon: AnonymizedReport = TextAnonymizer.anonymize(raw_text)
        if not anon.success:
            return ProcessingResult(
                success=False,
                pdf_path=pdf_path,
                output_text="",
                anonymized_text="",
                patient_name=anon.patient_name,
                profile_used=anon.profile_used,
                mode=self.mode,
                error_message=anon.error_message,
            )

        # Step 3: Mode-dependent processing
        if self.mode == ProcessingMode.AI_ASSISTED:
            output_text = self._process_with_ai(anon.anonymized_text)
            ai_used = bool(output_text)
            if not output_text:
                # Fallback to local-only on AI failure
                logger.warning(
                    "AI non disponibile, uso processazione locale per %s",
                    pdf_path.name,
                )
                output_text = anon.anonymized_text
        else:
            output_text = anon.anonymized_text
            ai_used = False

        return ProcessingResult(
            success=True,
            pdf_path=pdf_path,
            output_text=output_text,
            anonymized_text=anon.anonymized_text,
            patient_name=anon.patient_name,
            profile_used=anon.profile_used,
            mode=self.mode,
            ai_used=ai_used,
        )

    def _process_with_ai(self, anonymized_text: str) -> str:
        """Send anonymized text to an LLM for structured analysis.

        Returns the LLM response, or empty string on failure (triggering
        automatic fallback to local-only mode).
        """
        if self.llm_config is None or not self.llm_config.provider:
            logger.warning("Configurazione LLM mancante")
            return ""
        analyzer = LLMAnalyzer(self.llm_config)
        return analyzer.analyze(anonymized_text)

    @staticmethod
    def save_result(
        result: ProcessingResult,
        output_dir: Path,
        filename_base: str,
    ) -> Path | None:
        """Save processed text to a .txt file.

        Args:
            result: The processing result to save.
            output_dir: Directory to save the text file in.
            filename_base: Base filename without extension
                           (e.g. 'RSSMRA80A01F205X_ROSSI_MARIO_LAB').

        Returns:
            Path to the saved file, or None on failure.
        """
        if not result.success or not result.output_text.strip():
            return None

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{filename_base}.txt"

            # Handle filename collisions
            counter = 1
            while output_path.exists():
                output_path = output_dir / f"{filename_base}_{counter}.txt"
                counter += 1

            output_path.write_text(result.output_text, encoding="utf-8")
            logger.info("Testo salvato: %s", output_path)
            return output_path
        except Exception as e:
            logger.error("Errore salvataggio testo %s: %s", filename_base, e)
            return None
