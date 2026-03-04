import os
import subprocess
import sys
import threading

from app_paths import paths

# Set Playwright browsers path to a writable location
# In installed mode, app_dir is under Program Files (read-only);
# use AppData instead so Chromium can be downloaded without admin rights.
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    if paths._data_dir != paths.app_dir:
        # Installed mode: writable data directory (%APPDATA%/FSE Processor)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(paths._data_dir / "playwright_browsers")
    else:
        # Portable mode: relative to app
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(paths.app_dir / "playwright_browsers")

from config import Config
from logger_module import ProcessingLogger
from email_client import EmailClient
from browser_automation import FSEBrowser, BrowserCDPNotActive
from file_manager import FileManager
from processing_summary import FailedDownload, ProcessingSummary
from text_processing import TextProcessor, ProcessingMode, LLMConfig


def run_processing(config: Config, logger: ProcessingLogger, stop_event: threading.Event | None = None,
                    allowed_types: set[str] | None = None,
                    on_cdp_restart_needed: callable = None,
                    on_headless_no_auth: callable = None) -> ProcessingSummary | None:
    """Core processing logic, reusable from CLI and GUI.

    on_cdp_restart_needed: optional callback(BrowserCDPNotActive) -> bool.
        Called when browser needs restart for CDP. Return True to restart.
    on_headless_no_auth: optional callback() -> None.
        Called when headless is active but no SISS session exists.
        Used by the GUI to show a blocking warning dialog.
    Returns ProcessingSummary with results, or None on early exit.
    """

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    logger.info("=== Avvio processamento FSE ===")

    # Connect to IMAP
    email_client = EmailClient(config, logger)
    try:
        email_client.connect()
    except Exception as e:
        logger.error(f"Connessione IMAP fallita: {e}")
        return

    if stopped():
        email_client.disconnect()
        logger.info("Processamento interrotto dall'utente")
        return

    # Fetch unread emails
    try:
        emails = email_client.fetch_unread_emails()
    except Exception as e:
        logger.error(f"Errore fetch email: {e}")
        email_client.disconnect()
        return

    logger.emails_found = len(emails)
    if not emails:
        logger.info("Nessuna email da processare")
        email_client.disconnect()
        logger.save_summary()
        return

    if stopped():
        email_client.disconnect()
        logger.info("Processamento interrotto dall'utente")
        return

    # Initialize text processor
    text_processor = None
    text_dir = config.text_dir
    if config.process_text:
        if not text_dir:
            text_dir = config.download_dir / "testi"
            logger.info(f"TEXT_DIR non configurata, uso default: {text_dir}")
        mode_str = config.processing_mode
        if mode_str == "ai" and config.llm_provider:
            mode = ProcessingMode.AI_ASSISTED
            llm_config = LLMConfig(
                provider=config.llm_provider,
                api_key=config.llm_api_key,
                model=config.llm_model,
                timeout=config.llm_timeout,
                base_url=config.llm_base_url,
            )
            text_processor = TextProcessor(mode, llm_config=llm_config)
        else:
            mode = ProcessingMode.LOCAL_ONLY
            text_processor = TextProcessor(mode)
        logger.info(f"Processazione testo attiva (modalita': {mode.value})")

    # Start browser and perform manual login
    file_manager = FileManager(config, logger)
    browser = FSEBrowser(config, logger)
    try:
        try:
            browser.start()
        except BrowserCDPNotActive as e:
            if on_cdp_restart_needed and on_cdp_restart_needed(e):
                browser.restart_browser_with_cdp(
                    e.process_name, e.exe_path, e.port
                )
            else:
                raise
        # Block headless mode if no active SISS session exists
        if not browser.check_headless_auth():
            if on_headless_no_auth:
                on_headless_no_auth()
            else:
                logger.error(
                    "Headless attivo ma nessuna sessione SISS valida. "
                    "Disattiva la modalita' headless o effettua prima il "
                    "login con il browser visibile."
                )
            browser.stop()
            email_client.disconnect()
            return
        browser.wait_for_manual_login(stop_event=stop_event)
    except Exception as e:
        logger.error(f"Avvio browser fallito: {e}")
        email_client.disconnect()
        return

    # Deferred processing: collect PDFs first, process text later
    deferred = config.deferred_processing and text_processor is not None
    pending_text_files: list = []

    # Process each email
    session_pdfs: list[str] = []
    failures: list[FailedDownload] = []
    try:
        for email_data in emails:
            if stopped():
                logger.info("Processamento interrotto dall'utente")
                break

            logger.info(
                f"--- Processo email: {email_data.patient_name} "
                f"(CF: {email_data.codice_fiscale}) ---"
            )

            # Navigate FSE and get documents
            doc_results = browser.process_patient(
                email_data.fse_link, email_data.patient_name,
                email_data.codice_fiscale, stop_event=stop_event,
                allowed_types=allowed_types,
            )

            all_ok = True
            for result in doc_results:
                if result.skipped:
                    logger.documents_skipped += 1
                    continue

                if result.error or not result.download_path:
                    all_ok = False
                    failures.append(FailedDownload(
                        patient_name=email_data.patient_name,
                        codice_fiscale=email_data.codice_fiscale,
                        disciplina=result.disciplina,
                        date_text=result.date_text,
                        error=result.error or "Download fallito",
                    ))
                    file_manager.add_failure(
                        patient_name=email_data.patient_name,
                        codice_fiscale=email_data.codice_fiscale,
                        disciplina=result.disciplina,
                        date_text=result.date_text,
                        fse_link=email_data.fse_link,
                    )
                    continue

                # Rename downloaded file
                logger.documents_downloaded += 1
                renamed = file_manager.rename_download(
                    download_path=result.download_path,
                    patient_name=email_data.patient_name,
                    codice_fiscale=email_data.codice_fiscale,
                    disciplina=result.disciplina,
                    fse_link=email_data.fse_link,
                    date_text=result.date_text,
                )
                if renamed:
                    logger.documents_renamed += 1
                    session_pdfs.append(str(renamed))

                    # Text processing (immediate or deferred)
                    if text_processor is not None:
                        if deferred:
                            pending_text_files.append(renamed)
                        else:
                            try:
                                tp_result = text_processor.process(renamed)
                                if tp_result.success:
                                    saved = TextProcessor.save_result(
                                        tp_result, text_dir, renamed.stem,
                                    )
                                    if saved:
                                        logger.info(f"Testo salvato: {saved.name}")
                                else:
                                    logger.warning(
                                        f"Estrazione testo fallita per {renamed.name}: "
                                        f"{tp_result.error_message}"
                                    )
                            except Exception as e:
                                logger.warning(f"Errore processazione testo {renamed.name}: {e}")

            # Post-download actions (only if all documents processed successfully)
            if all_ok:
                try:
                    if config.mark_as_read:
                        email_client.mark_as_read(email_data.uid, email_data.folder)
                    else:
                        email_client.track_uid_locally(email_data.uid, email_data.folder)
                    if config.delete_after_processing:
                        email_client.delete_message(email_data.uid, email_data.folder)
                    logger.emails_processed += 1
                except Exception as e:
                    logger.error(f"Errore post-elaborazione email UID {email_data.uid}: {e}")
            else:
                logger.emails_skipped += 1
                logger.warning(
                    f"Email per {email_data.patient_name} NON marcata come letta "
                    f"(errori nel download)"
                )

    finally:
        browser.stop()
        email_client.disconnect()

    # Deferred text processing phase
    text_processed = text_errors = 0
    if deferred and pending_text_files and not stopped():
        logger.info(f"=== Avvio elaborazione testi ({len(pending_text_files)} referti) ===")
        text_processed, text_errors = TextProcessor.process_batch(
            text_processor, pending_text_files, text_dir, logger.info,
            stop_check=stopped,
        )
        logger.info(f"=== Elaborazione testi completata: {text_processed} OK, {text_errors} errori ===")

    # Save mapping, report, and summary
    file_manager.save_mappings()
    report_path = file_manager.save_referti_report()
    logger.save_summary()

    # Open only session PDFs in PDF reader
    if session_pdfs and not stopped():
        if config.pdf_reader == "default" or not config.pdf_reader:
            logger.info(f"Apertura di {len(session_pdfs)} PDF con il lettore predefinito...")
            for pdf in session_pdfs:
                os.startfile(pdf)
        else:
            logger.info(f"Apertura di {len(session_pdfs)} PDF in {config.pdf_reader}...")
            subprocess.Popen([config.pdf_reader] + session_pdfs)

    logger.info("=== Processamento completato ===")

    return ProcessingSummary(
        downloaded=logger.documents_downloaded,
        skipped=logger.documents_skipped,
        duplicates=file_manager.duplicates_count,
        errors=len(failures),
        emails_found=logger.emails_found,
        emails_processed=logger.emails_processed,
        failures=failures,
        report_path=report_path,
        text_processed=text_processed,
        text_errors=text_errors,
        deferred_mode=deferred,
    )


def main() -> None:
    try:
        config = Config.load()
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERRORE] Configurazione: {e}")
        sys.exit(1)

    logger = ProcessingLogger(config.log_dir)
    run_processing(config, logger)


if __name__ == "__main__":
    main()
