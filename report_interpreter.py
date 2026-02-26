"""Interactive medical report interpreter dialog.

Allows users to paste or load raw report text, select an AI provider,
and receive a structured analysis with severity-coded findings (+/++/+++).
"""

from __future__ import annotations

import html
import logging
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from text_processing.llm_analyzer import (
    DEFAULT_MODELS,
    LABEL_TO_PROVIDER,
    LLMAnalyzer,
    LLMConfig,
    PROVIDER_LABELS,
)
from text_processing.text_anonymizer import TextAnonymizer
from text_processing.text_processor import TextProcessor, ProcessingMode, ProcessingResult

logger = logging.getLogger(__name__)

# Model suggestions per provider
_MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "claude_api": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
    "openai_api": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
    "gemini_api": ["gemini-2.0-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
    "mistral_api": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
    "claude_cli": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-6"],
    "custom_url": [],
}


class _Signals(QObject):
    """Thread-safe signals for background LLM calls."""

    analysis_done = Signal(str)
    analysis_error = Signal(str)
    status = Signal(str)
    call_on_main = Signal(object)


class ReportInterpreterDialog(QDialog):
    """Dialog for interactive medical report interpretation via LLM."""

    def __init__(self, parent=None, *, llm_config: LLMConfig | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Interpreta Referto")
        self.resize(900, 700)
        self.setMinimumSize(700, 500)

        self._sig = _Signals()
        self._sig.analysis_done.connect(self._on_done)
        self._sig.analysis_error.connect(self._on_error)
        self._sig.status.connect(self._on_status)
        self._sig.call_on_main.connect(lambda fn: fn())

        self._running = False
        self._last_plain_result = ""  # plain text for copy/save
        self._build_ui()
        if llm_config:
            self._prefill(llm_config)

    # ----------------------------------------------------------------
    # UI construction
    # ----------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # === Provider settings ===
        provider_group = QGroupBox("Provider AI")
        pg = QGridLayout(provider_group)
        pg.setColumnStretch(1, 1)
        pg.setColumnStretch(3, 1)

        # Row 0: Provider + API Key
        pg.addWidget(QLabel("Provider:"), 0, 0)
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(list(PROVIDER_LABELS.values()))
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        pg.addWidget(self._provider_combo, 0, 1)

        pg.addWidget(QLabel("API Key:"), 0, 2)
        key_row = QHBoxLayout()
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("API key")
        key_row.addWidget(self._api_key, 1)
        self._show_key_btn = QPushButton("Mostra")
        self._show_key_btn.setFixedWidth(60)
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.toggled.connect(
            lambda on: self._api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self._show_key_btn)
        pg.addLayout(key_row, 0, 3)

        # Row 1: Model + Base URL + Test
        pg.addWidget(QLabel("Modello:"), 1, 0)
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setToolTip("Seleziona un modello predefinito o inserisci un nome personalizzato")
        pg.addWidget(self._model_combo, 1, 1)

        self._base_url_label = QLabel("URL endpoint:")
        pg.addWidget(self._base_url_label, 1, 2)
        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText("https://your-server.com")
        pg.addWidget(self._base_url, 1, 3)

        self._test_btn = QPushButton("Testa")
        self._test_btn.setFixedWidth(60)
        self._test_btn.clicked.connect(self._test_connection)
        pg.addWidget(self._test_btn, 1, 4)

        layout.addWidget(provider_group)

        # === Splitter: Input / Output ===
        splitter = QSplitter(Qt.Orientation.Vertical)

        # -- Input area --
        input_w = QWidget()
        il = QVBoxLayout(input_w)
        il.setContentsMargins(0, 0, 0, 0)

        ih = QHBoxLayout()
        ih.addWidget(QLabel("<b>Testo del referto</b>"))
        ih.addStretch()
        btn_pdf = QPushButton("Carica PDF")
        btn_pdf.clicked.connect(self._load_pdf)
        ih.addWidget(btn_pdf)
        btn_txt = QPushButton("Carica TXT")
        btn_txt.clicked.connect(self._load_txt)
        ih.addWidget(btn_txt)
        btn_paste = QPushButton("Incolla")
        btn_paste.clicked.connect(self._paste)
        ih.addWidget(btn_paste)
        btn_clear = QPushButton("Pulisci")
        btn_clear.clicked.connect(lambda: self._input_text.clear())
        ih.addWidget(btn_clear)
        il.addLayout(ih)

        self._input_text = QTextEdit()
        self._input_text.setPlaceholderText(
            "Incolla qui il testo grezzo del referto oppure caricalo da file..."
        )
        il.addWidget(self._input_text)
        splitter.addWidget(input_w)

        # -- Output area --
        output_w = QWidget()
        ol = QVBoxLayout(output_w)
        ol.setContentsMargins(0, 0, 0, 0)

        oh = QHBoxLayout()
        oh.addWidget(QLabel("<b>Analisi strutturata</b>"))
        oh.addStretch()
        btn_copy = QPushButton("Copia")
        btn_copy.clicked.connect(self._copy_result)
        oh.addWidget(btn_copy)
        btn_save = QPushButton("Salva")
        btn_save.clicked.connect(self._save_result)
        oh.addWidget(btn_save)
        ol.addLayout(oh)

        self._output_text = QTextEdit()
        self._output_text.setReadOnly(True)
        ol.addWidget(self._output_text)
        splitter.addWidget(output_w)

        splitter.setSizes([250, 400])
        layout.addWidget(splitter, 1)

        # === Bottom bar ===
        bottom = QHBoxLayout()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #6b7b8d; font-style: italic;")
        bottom.addWidget(self._status_label, 1)

        self._analyze_btn = QPushButton("Anonimizza e Analizza")
        self._analyze_btn.setStyleSheet("font-weight: bold; padding: 6px 16px;")
        self._analyze_btn.clicked.connect(self._run_analysis)
        bottom.addWidget(self._analyze_btn)

        btn_close = QPushButton("Chiudi")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

        # Initial provider state
        self._on_provider_changed(self._provider_combo.currentText())

    # ----------------------------------------------------------------
    # Pre-fill from saved settings
    # ----------------------------------------------------------------

    def _prefill(self, config: LLMConfig) -> None:
        if config.provider and config.provider in PROVIDER_LABELS:
            self._provider_combo.setCurrentText(PROVIDER_LABELS[config.provider])
        if config.api_key:
            self._api_key.setText(config.api_key)
        if config.model:
            self._model_combo.setCurrentText(config.model)
        if config.base_url:
            self._base_url.setText(config.base_url)

    # ----------------------------------------------------------------
    # Provider change
    # ----------------------------------------------------------------

    def _on_provider_changed(self, label: str) -> None:
        provider = LABEL_TO_PROVIDER.get(label, "")
        # Base URL visibility
        is_custom = provider == "custom_url"
        self._base_url_label.setVisible(is_custom)
        self._base_url.setVisible(is_custom)
        # Reset API key — each provider needs its own key
        self._api_key.clear()
        needs_key = provider != "claude_cli"
        self._api_key.setEnabled(needs_key)
        self._show_key_btn.setEnabled(needs_key)
        if not needs_key:
            self._api_key.setPlaceholderText("(non necessaria)")
        else:
            self._api_key.setPlaceholderText("API key")
        # Model suggestions — reset to new provider's default
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.clearEditText()
        suggestions = _MODEL_SUGGESTIONS.get(provider, [])
        if suggestions:
            self._model_combo.addItems(suggestions)
            default = DEFAULT_MODELS.get(provider, "")
            if default in suggestions:
                self._model_combo.setCurrentText(default)
        self._model_combo.blockSignals(False)

    # ----------------------------------------------------------------
    # Build LLMConfig from current fields
    # ----------------------------------------------------------------

    def _current_config(self) -> LLMConfig:
        label = self._provider_combo.currentText()
        provider = LABEL_TO_PROVIDER.get(label, "")
        return LLMConfig(
            provider=provider,
            api_key=self._api_key.text().strip(),
            model=self._model_combo.currentText().strip(),
            timeout=120,
            base_url=self._base_url.text().strip(),
        )

    # ----------------------------------------------------------------
    # Load / paste actions
    # ----------------------------------------------------------------

    def _load_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleziona PDF", "", "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            from text_processing.pdf_text_extractor import PdfTextExtractor

            text = PdfTextExtractor.extract_simple(Path(path))
            if text and text.strip():
                self._input_text.setPlainText(text)
                self._status_label.setText(f"Caricato: {Path(path).name}")
            else:
                QMessageBox.warning(
                    self, "PDF vuoto",
                    "Nessun testo estraibile dal PDF selezionato.",
                )
        except Exception as e:
            QMessageBox.critical(
                self, "Errore",
                f"Errore nell'estrazione del testo:\n{e}",
            )

    def _load_txt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleziona file di testo", "", "Testo (*.txt);;Tutti (*.*)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
            self._input_text.setPlainText(text)
            self._status_label.setText(f"Caricato: {Path(path).name}")
        except Exception as e:
            QMessageBox.critical(
                self, "Errore",
                f"Errore nella lettura del file:\n{e}",
            )

    def _paste(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self._input_text.setPlainText(text)
            self._status_label.setText("Testo incollato dagli appunti")
        else:
            self._status_label.setText("Appunti vuoti")

    def _copy_result(self) -> None:
        text = self._last_plain_result
        if text:
            QApplication.clipboard().setText(text)
            self._status_label.setText("Risultato copiato negli appunti")

    def _save_result(self) -> None:
        text = self._last_plain_result
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Salva risultato", "", "Testo (*.txt)"
        )
        if path:
            Path(path).write_text(text, encoding="utf-8")
            self._status_label.setText(f"Salvato: {Path(path).name}")

    # ----------------------------------------------------------------
    # Analysis
    # ----------------------------------------------------------------

    def _run_analysis(self) -> None:
        if self._running:
            return

        raw_text = self._input_text.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(
                self, "Testo mancante",
                "Inserisci il testo del referto da analizzare.",
            )
            return

        config = self._current_config()
        if not config.provider:
            QMessageBox.warning(
                self, "Provider mancante",
                "Seleziona un provider AI.",
            )
            return
        if config.provider != "claude_cli" and not config.api_key:
            QMessageBox.warning(
                self, "API Key mancante",
                "Inserisci l'API key per il provider selezionato.",
            )
            return

        self._running = True
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.setText("Analisi in corso...")
        self._output_text.clear()
        self._last_plain_result = ""

        threading.Thread(
            target=self._analysis_worker,
            args=(raw_text, config),
            daemon=True,
        ).start()

    def _analysis_worker(self, raw_text: str, config: LLMConfig) -> None:
        try:
            # Step 1: Anonymize
            self._sig.status.emit("Anonimizzazione testo...")
            result = TextAnonymizer.anonymize(raw_text)
            if not result.success or not result.anonymized_text.strip():
                self._sig.analysis_error.emit(
                    f"Anonimizzazione fallita: "
                    f"{result.error_message or 'testo vuoto dopo filtraggio'}"
                )
                return

            # Step 2: Send to LLM
            provider_label = PROVIDER_LABELS.get(config.provider, config.provider)
            self._sig.status.emit(f"Invio a {provider_label}...")
            analyzer = LLMAnalyzer(config)
            response = analyzer.analyze(result.anonymized_text)

            if not response:
                self._sig.analysis_error.emit(
                    "Il LLM non ha restituito alcun risultato."
                )
                return

            self._sig.analysis_done.emit(response)

        except Exception as e:
            logger.exception("Errore durante l'analisi")
            self._sig.analysis_error.emit(str(e))

    # ----------------------------------------------------------------
    # Signal handlers (main thread)
    # ----------------------------------------------------------------

    def _on_done(self, response: str) -> None:
        self._running = False
        self._analyze_btn.setEnabled(True)
        self._analyze_btn.setText("Anonimizza e Analizza")
        self._last_plain_result = response
        self._output_text.setHtml(_format_response_html(response))
        self._status_label.setText("Analisi completata")

    def _on_error(self, msg: str) -> None:
        self._running = False
        self._analyze_btn.setEnabled(True)
        self._analyze_btn.setText("Anonimizza e Analizza")
        self._status_label.setText(f"Errore: {msg}")
        QMessageBox.critical(self, "Errore analisi", msg)

    def _on_status(self, msg: str) -> None:
        self._status_label.setText(msg)

    # ----------------------------------------------------------------
    # Test connection
    # ----------------------------------------------------------------

    def _test_connection(self) -> None:
        config = self._current_config()
        if not config.provider:
            return
        self._test_btn.setEnabled(False)
        self._test_btn.setText("...")

        def worker():
            try:
                analyzer = LLMAnalyzer(config)
                ok = analyzer.is_available()
                label = PROVIDER_LABELS.get(config.provider, config.provider)
                if ok:
                    self._sig.status.emit(f"Connessione a {label} riuscita!")
                else:
                    self._sig.status.emit(f"Connessione a {label} fallita")
            except Exception as e:
                self._sig.status.emit(f"Errore test: {e}")
            finally:
                self._sig.call_on_main.emit(lambda: self._test_btn.setEnabled(True))
                self._sig.call_on_main.emit(lambda: self._test_btn.setText("Testa"))

        threading.Thread(target=worker, daemon=True).start()


# ====================================================================
# HTML formatting for severity-coded output
# ====================================================================

def _format_response_html(text: str) -> str:
    """Convert LLM response to HTML with colored severity markers."""
    escaped = html.escape(text)

    # Color severity markers (longest first to avoid substring conflicts)
    escaped = escaped.replace(
        "(+++)",
        '<span style="background-color: #ffcdd2; color: #c62828; font-weight: bold;">'
        "(+++)</span>",
    )
    escaped = escaped.replace(
        "(++)",
        '<span style="background-color: #ffe0b2; color: #e65100; font-weight: bold;">'
        "(++)</span>",
    )
    escaped = escaped.replace(
        "(+)",
        '<span style="background-color: #dcedc8; color: #33691e; font-weight: bold;">'
        "(+)</span>",
    )

    # Section separator
    escaped = escaped.replace(
        "____" + "_" * 76,
        '<hr style="border: none; border-top: 2px solid #bdbdbd; margin: 12px 0;">',
    )

    # Bold section headers
    for header in (
        "REPERTI PATOLOGICI SIGNIFICATIVI",
        "TESTO COMPLETO DEL REFERTO",
    ):
        escaped = escaped.replace(
            header,
            f'<b style="font-size: 13px; color: #1565c0;">{header}</b>',
        )

    # Newlines to <br>
    escaped = escaped.replace("\n", "<br>")

    return (
        '<div style="font-family: Segoe UI, sans-serif; font-size: 12px; '
        f'line-height: 1.6; padding: 8px;">{escaped}</div>'
    )


# ====================================================================
# Report Picker Dialog - batch AI analysis of selected reports
# ====================================================================

def _is_lab_report(pdf_path: Path) -> bool:
    """Check if a PDF is a LAB report (excluded from AI analysis)."""
    stem = pdf_path.stem.upper()
    if stem.endswith("_LAB"):
        return True
    # Handle numbered variants like _1_LAB, _2_LAB
    parts = stem.rsplit("_", maxsplit=2)
    if len(parts) >= 2 and parts[-1] == "LAB":
        return True
    return False


def _display_name_from_pdf(pdf_path: Path) -> str:
    """Build a human-readable display name from a PDF filename.

    Example: 'RSSMRA80A01F205X_ROSSI_MARIO_SPEC.pdf' -> 'ROSSI MARIO - SPEC'
    """
    stem = pdf_path.stem
    parts = stem.split("_")
    if len(parts) >= 4:
        # CF_COGNOME_NOME_TYPE or CF_COGNOME_NOME_N_TYPE
        cf = parts[0]
        doc_type = parts[-1]
        name_parts = parts[1:-1]
        # If last name part is a digit (numbered variant), include it in type
        if name_parts and name_parts[-1].isdigit():
            doc_type = f"{name_parts.pop()}_{doc_type}"
        name = " ".join(name_parts)
        return f"{name} - {doc_type}"
    return stem


class _PickerSignals(QObject):
    """Thread-safe signals for the batch processing worker."""

    progress = Signal(int, int)        # (current, total)
    file_done = Signal(str, bool)      # (filename, success)
    all_done = Signal(int, int)        # (succeeded, total)
    error = Signal(str)


class ReportPickerDialog(QDialog):
    """Modal dialog for selecting downloaded reports to send to AI analysis."""

    def __init__(
        self,
        parent=None,
        *,
        download_dir: str,
        text_dir: str,
        llm_config: LLMConfig,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scegli referti da analizzare")
        self.resize(600, 500)
        self.setMinimumSize(450, 350)

        self._download_dir = Path(download_dir) if download_dir else None
        self._text_dir = Path(text_dir) if text_dir else None
        self._llm_config = llm_config
        self._running = False

        self._sig = _PickerSignals()
        self._sig.progress.connect(self._on_progress)
        self._sig.file_done.connect(self._on_file_done)
        self._sig.all_done.connect(self._on_all_done)
        self._sig.error.connect(self._on_error)

        self._build_ui()
        self._populate_list()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header = QLabel(
            "<b>Seleziona i referti da inviare all'analisi A.I.</b><br>"
            "<small>I referti di laboratorio (_LAB) sono esclusi automaticamente.</small>"
        )
        layout.addWidget(header)

        # Selection buttons
        btn_row = QHBoxLayout()
        btn_all = QPushButton("Seleziona tutti")
        btn_all.clicked.connect(self._select_all)
        btn_row.addWidget(btn_all)
        btn_none = QPushButton("Deseleziona tutti")
        btn_none.clicked.connect(self._deselect_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Report list with checkboxes
        self._list_widget = QListWidget()
        layout.addWidget(self._list_widget, 1)

        # Progress bar (hidden initially)
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # Status label
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #6b7b8d; font-style: italic;")
        layout.addWidget(self._status_label)

        # Bottom buttons
        bottom = QHBoxLayout()
        bottom.addStretch()
        self._analyze_btn = QPushButton("Analizza selezionati")
        self._analyze_btn.setStyleSheet("font-weight: bold; padding: 6px 16px;")
        self._analyze_btn.clicked.connect(self._run_batch)
        bottom.addWidget(self._analyze_btn)
        self._close_btn = QPushButton("Chiudi")
        self._close_btn.clicked.connect(self.close)
        bottom.addWidget(self._close_btn)
        layout.addLayout(bottom)

    def _populate_list(self) -> None:
        """Scan download directory and list non-LAB PDFs."""
        self._list_widget.clear()

        if not self._download_dir or not self._download_dir.is_dir():
            self._status_label.setText("Cartella download non trovata o non configurata.")
            self._analyze_btn.setEnabled(False)
            return

        pdfs = sorted(self._download_dir.glob("*.pdf"), key=lambda p: p.name)
        pdfs = [p for p in pdfs if not _is_lab_report(p)]

        if not pdfs:
            self._status_label.setText("Nessun referto trovato (esclusi LAB).")
            self._analyze_btn.setEnabled(False)
            return

        for pdf in pdfs:
            item = QListWidgetItem()
            item.setText(_display_name_from_pdf(pdf))
            item.setData(Qt.ItemDataRole.UserRole, str(pdf))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._list_widget.addItem(item)

        self._status_label.setText(f"{len(pdfs)} referti disponibili")

    def _select_all(self) -> None:
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _get_selected_paths(self) -> list[Path]:
        """Return paths of all checked items."""
        paths = []
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))
        return paths

    def _run_batch(self) -> None:
        """Start batch AI analysis of selected reports."""
        if self._running:
            return

        selected = self._get_selected_paths()
        if not selected:
            QMessageBox.warning(
                self, "Nessuna selezione",
                "Seleziona almeno un referto da analizzare.",
            )
            return

        if not self._llm_config.provider:
            QMessageBox.warning(
                self, "Provider mancante",
                "Configura un provider AI nelle Impostazioni.",
            )
            return

        if self._llm_config.provider != "claude_cli" and not self._llm_config.api_key:
            QMessageBox.warning(
                self, "API Key mancante",
                "Configura l'API key nelle Impostazioni.",
            )
            return

        self._running = True
        self._analyze_btn.setEnabled(False)
        self._close_btn.setEnabled(False)
        self._list_widget.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(len(selected))
        self._progress_bar.setValue(0)

        threading.Thread(
            target=self._batch_worker,
            args=(selected,),
            daemon=True,
        ).start()

    def _batch_worker(self, pdf_paths: list[Path]) -> None:
        """Background worker: process each selected PDF through the AI pipeline."""
        processor = TextProcessor(
            mode=ProcessingMode.AI_ASSISTED,
            llm_config=self._llm_config,
        )
        succeeded = 0
        total = len(pdf_paths)

        for i, pdf_path in enumerate(pdf_paths):
            try:
                self._sig.progress.emit(i, total)
                result = processor.process(pdf_path)

                if result.success and result.output_text.strip():
                    # Save to text dir if configured
                    if self._text_dir:
                        TextProcessor.save_result(
                            result, self._text_dir, pdf_path.stem
                        )
                    succeeded += 1
                    self._sig.file_done.emit(pdf_path.name, True)
                else:
                    err = result.error_message or "testo vuoto"
                    logger.warning("Analisi fallita per %s: %s", pdf_path.name, err)
                    self._sig.file_done.emit(pdf_path.name, False)

            except Exception as e:
                logger.exception("Errore analisi %s", pdf_path.name)
                self._sig.file_done.emit(pdf_path.name, False)

        self._sig.all_done.emit(succeeded, total)

    # ----------------------------------------------------------------
    # Signal handlers (main thread)
    # ----------------------------------------------------------------

    def _on_progress(self, current: int, total: int) -> None:
        self._progress_bar.setValue(current)
        self._status_label.setText(f"Analisi {current + 1} di {total}...")

    def _on_file_done(self, filename: str, success: bool) -> None:
        status = "OK" if success else "ERRORE"
        logger.info("Report %s: %s", filename, status)

    def _on_all_done(self, succeeded: int, total: int) -> None:
        self._running = False
        self._analyze_btn.setEnabled(True)
        self._close_btn.setEnabled(True)
        self._list_widget.setEnabled(True)
        self._progress_bar.setValue(total)

        failed = total - succeeded
        if failed == 0:
            self._status_label.setText(f"Completato: {succeeded}/{total} referti analizzati")
            msg = f"Analisi completata con successo.\n{succeeded} referti analizzati."
            if self._text_dir:
                msg += f"\n\nTesti salvati in:\n{self._text_dir}"
            QMessageBox.information(self, "Analisi completata", msg)
        else:
            self._status_label.setText(
                f"Completato: {succeeded}/{total} OK, {failed} errori"
            )
            msg = (
                f"Analisi completata.\n"
                f"Riusciti: {succeeded}/{total}\n"
                f"Errori: {failed}/{total}"
            )
            if self._text_dir:
                msg += f"\n\nTesti salvati in:\n{self._text_dir}"
            QMessageBox.warning(self, "Analisi completata con errori", msg)

    def _on_error(self, msg: str) -> None:
        self._running = False
        self._analyze_btn.setEnabled(True)
        self._close_btn.setEnabled(True)
        self._list_widget.setEnabled(True)
        self._progress_bar.setVisible(False)
        QMessageBox.critical(self, "Errore", msg)
