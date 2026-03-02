"""Side-by-side comparison of original vs anonymized report text.

Allows the user to pick a downloaded PDF, re-extracts the raw text,
re-anonymizes it on the fly, and shows a visual diff highlighting
which lines were removed (redacted) by the anonymizer.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from text_processing.pdf_text_extractor import PdfTextExtractor
from text_processing.text_anonymizer import AnonymizedReport, TextAnonymizer
from text_processing.text_processor import _detect_doc_type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _display_name_from_pdf(pdf_path: Path) -> str:
    """Build a human-readable display name from a PDF filename.

    Example: 'RSSMRA80A01F205X_ROSSI_MARIO_SPEC.pdf' -> 'ROSSI MARIO - SPEC'
    """
    stem = pdf_path.stem
    parts = stem.split("_")
    if len(parts) >= 4:
        doc_type = parts[-1]
        name_parts = parts[1:-1]
        if name_parts and name_parts[-1].isdigit():
            doc_type = f"{name_parts.pop()}_{doc_type}"
        name = " ".join(name_parts)
        return f"{name} - {doc_type}"
    return stem


# ---------------------------------------------------------------------------
# Thread-safe signals
# ---------------------------------------------------------------------------

class _ComparisonSignals(QObject):
    """Emitted from the worker thread, connected in the GUI thread."""

    finished = Signal(str, object)  # (raw_text, AnonymizedReport | None)
    error = Signal(str)             # error message


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class TextComparisonDialog(QDialog):
    """Dialog for comparing original vs anonymized report text side-by-side."""

    _REDACTED_BG = QColor("#FFCCCC")   # light pink background
    _REDACTED_FG = QColor("#CC0000")   # dark red text

    def __init__(self, parent=None, *, download_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Visiona Testi - Confronto Originale / Anonimizzato")
        self.resize(1000, 700)
        self.setMinimumSize(700, 500)

        self._download_dir = Path(download_dir) if download_dir else None
        self._running = False

        self._sig = _ComparisonSignals()
        self._sig.finished.connect(self._on_comparison_ready)
        self._sig.error.connect(self._on_error)

        self._build_ui()
        self._populate_list()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # --- Top: PDF picker ---
        header = QLabel(
            "<b>Seleziona un referto e premi Confronta per visualizzare "
            "le differenze tra testo originale e anonimizzato.</b>"
        )
        layout.addWidget(header)

        btn_row = QHBoxLayout()
        btn_all = QPushButton("Seleziona tutti")
        btn_all.clicked.connect(self._select_all)
        btn_row.addWidget(btn_all)
        btn_none = QPushButton("Deseleziona tutti")
        btn_none.clicked.connect(self._deselect_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._list_widget = QListWidget()
        self._list_widget.setMaximumHeight(160)
        self._list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list_widget)

        compare_row = QHBoxLayout()
        self._compare_btn = QPushButton("Confronta selezionato")
        self._compare_btn.setStyleSheet("font-weight: bold; padding: 6px 16px;")
        self._compare_btn.clicked.connect(self._run_comparison)
        compare_row.addWidget(self._compare_btn)
        compare_row.addStretch()
        layout.addLayout(compare_row)

        # --- Middle: side-by-side text panels ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel: original text
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("<b>Testo Originale</b>")
        left_label.setStyleSheet("color: #CC0000;")
        left_layout.addWidget(left_label)
        self._original_edit = QTextEdit()
        self._original_edit.setReadOnly(True)
        self._original_edit.setFont(QFont("Consolas", 9))
        left_layout.addWidget(self._original_edit)
        splitter.addWidget(left_widget)

        # Right panel: anonymized text
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("<b>Testo Anonimizzato</b>")
        right_label.setStyleSheet("color: #2E7D32;")
        right_layout.addWidget(right_label)
        self._anon_edit = QTextEdit()
        self._anon_edit.setReadOnly(True)
        self._anon_edit.setFont(QFont("Consolas", 9))
        right_layout.addWidget(self._anon_edit)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # --- Bottom: status bar + close ---
        bottom = QHBoxLayout()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #6b7b8d; font-style: italic;")
        bottom.addWidget(self._status_label, 1)
        close_btn = QPushButton("Chiudi")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    # ------------------------------------------------------------------
    # Populate the PDF list
    # ------------------------------------------------------------------

    def _populate_list(self) -> None:
        self._list_widget.clear()

        if not self._download_dir or not self._download_dir.is_dir():
            self._status_label.setText("Cartella download non trovata o non configurata.")
            self._compare_btn.setEnabled(False)
            return

        pdfs = sorted(self._download_dir.glob("*.pdf"), key=lambda p: p.name)
        if not pdfs:
            self._status_label.setText("Nessun referto PDF trovato.")
            self._compare_btn.setEnabled(False)
            return

        for pdf in pdfs:
            item = QListWidgetItem()
            item.setText(_display_name_from_pdf(pdf))
            item.setData(Qt.ItemDataRole.UserRole, str(pdf))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._list_widget.addItem(item)

        self._status_label.setText(f"{len(pdfs)} referti disponibili")

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all(self) -> None:
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _get_first_selected_path(self) -> Path | None:
        """Return the path of the first checked item."""
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                return Path(item.data(Qt.ItemDataRole.UserRole))
        return None

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Double-click a PDF to immediately compare it."""
        # Uncheck all, check only this one, then compare
        self._deselect_all()
        item.setCheckState(Qt.CheckState.Checked)
        self._run_comparison()

    # ------------------------------------------------------------------
    # Comparison logic
    # ------------------------------------------------------------------

    def _run_comparison(self) -> None:
        if self._running:
            return

        pdf_path = self._get_first_selected_path()
        if pdf_path is None:
            QMessageBox.warning(
                self, "Nessuna selezione",
                "Seleziona almeno un referto da confrontare.",
            )
            return

        self._running = True
        self._compare_btn.setEnabled(False)
        self._status_label.setText(f"Elaborazione di {pdf_path.name} ...")

        threading.Thread(
            target=self._comparison_worker,
            args=(pdf_path,),
            daemon=True,
        ).start()

    def _comparison_worker(self, pdf_path: Path) -> None:
        """Background worker: extract + anonymize, then signal the GUI."""
        try:
            doc_type = _detect_doc_type(pdf_path)
            if doc_type == "LAB":
                raw_text = PdfTextExtractor.extract_tables(pdf_path)
            else:
                raw_text = PdfTextExtractor.extract_simple(pdf_path)
            if not raw_text.strip():
                raw_text = PdfTextExtractor.extract(pdf_path)

            if not raw_text.strip():
                self._sig.error.emit(f"Nessun testo estraibile da {pdf_path.name}")
                return

            anon: AnonymizedReport = TextAnonymizer.anonymize(raw_text)
            self._sig.finished.emit(raw_text, anon)

        except Exception as exc:
            logger.exception("Errore durante confronto testi per %s", pdf_path.name)
            self._sig.error.emit(str(exc))

    # ------------------------------------------------------------------
    # GUI callbacks (main thread)
    # ------------------------------------------------------------------

    def _on_comparison_ready(self, raw_text: str, anon: AnonymizedReport) -> None:
        self._running = False
        self._compare_btn.setEnabled(True)

        if anon is None or not anon.success:
            msg = anon.error_message if anon else "Errore sconosciuto"
            self._status_label.setText(f"Errore: {msg}")
            return

        # Build the set of redacted line numbers for fast lookup
        redacted_lines: dict[int, str] = {}
        if anon.redactions:
            for rd in anon.redactions:
                reason = rd.reason
                if rd.pattern:
                    reason = f"{rd.reason}: {rd.pattern[:60]}"
                redacted_lines[rd.line_number] = reason

        # --- Populate left panel (original) with highlighting ---
        self._original_edit.clear()
        cursor = self._original_edit.textCursor()

        redacted_fmt = QTextCharFormat()
        redacted_fmt.setBackground(self._REDACTED_BG)
        redacted_fmt.setForeground(self._REDACTED_FG)

        normal_fmt = QTextCharFormat()

        lines = raw_text.splitlines()
        for line_idx, line in enumerate(lines, start=1):
            if line_idx > 1:
                cursor.insertText("\n", normal_fmt)

            stripped = line.strip()
            if not stripped:
                cursor.insertText(line, normal_fmt)
                continue

            if line_idx in redacted_lines:
                # Redacted line: highlight in red with tooltip
                redacted_fmt.setToolTip(redacted_lines[line_idx])
                cursor.insertText(line, redacted_fmt)
            else:
                cursor.insertText(line, normal_fmt)

        self._original_edit.moveCursor(QTextCursor.MoveOperation.Start)

        # --- Populate right panel (anonymized) ---
        self._anon_edit.clear()
        self._anon_edit.setPlainText(anon.anonymized_text)
        self._anon_edit.moveCursor(QTextCursor.MoveOperation.Start)

        # --- Update status bar ---
        n_redactions = len(redacted_lines)
        self._status_label.setText(
            f"Profilo: {anon.profile_used} | "
            f"Redazioni: {n_redactions} righe | "
            f"Paziente: {anon.patient_name}"
        )

    def _on_error(self, msg: str) -> None:
        self._running = False
        self._compare_btn.setEnabled(True)
        self._status_label.setText(f"Errore: {msg}")
        QMessageBox.critical(self, "Errore", msg)
