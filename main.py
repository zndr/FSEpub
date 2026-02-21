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
from browser_automation import FSEBrowser
from file_manager import FileManager


def run_processing(config: Config, logger: ProcessingLogger, stop_event: threading.Event | None = None) -> None:
    """Core processing logic, reusable from CLI and GUI."""

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    logger.info("=== Avvio processamento FSE ===")

    # Connect to POP3
    email_client = EmailClient(config, logger)
    try:
        email_client.connect()
    except Exception as e:
        logger.error(f"Connessione POP3 fallita: {e}")
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

    # Start browser and perform manual login
    file_manager = FileManager(config, logger)
    browser = FSEBrowser(config, logger)
    try:
        browser.start()
        browser.wait_for_manual_login()
    except Exception as e:
        logger.error(f"Avvio browser fallito: {e}")
        email_client.disconnect()
        return

    # Process each email
    session_pdfs: list[str] = []
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
            )

            all_ok = True
            for result in doc_results:
                if result.skipped:
                    logger.documents_skipped += 1
                    continue

                if result.error or not result.download_path:
                    all_ok = False
                    continue

                # Rename downloaded file
                logger.documents_downloaded += 1
                renamed = file_manager.rename_download(
                    download_path=result.download_path,
                    patient_name=email_data.patient_name,
                    codice_fiscale=email_data.codice_fiscale,
                    disciplina=result.disciplina,
                    fse_link=email_data.fse_link,
                )
                if renamed:
                    logger.documents_renamed += 1
                    session_pdfs.append(str(renamed))

            # Mark email as read only if all documents processed successfully
            if all_ok:
                try:
                    email_client.mark_as_read(email_data.uid)
                    if config.delete_after_processing:
                        email_client.delete_message(email_data.uid)
                    logger.emails_processed += 1
                except Exception as e:
                    logger.error(f"Errore marcatura email UID {email_data.uid}: {e}")
            else:
                logger.emails_skipped += 1
                logger.warning(
                    f"Email per {email_data.patient_name} NON marcata come letta "
                    f"(errori nel download)"
                )

    finally:
        browser.stop()
        email_client.disconnect()

    # Save mapping, report, and summary
    file_manager.save_mappings()
    file_manager.save_referti_report()
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
