import sys

from config import Config
from logger_module import ProcessingLogger
from email_client import EmailClient
from browser_automation import FSEBrowser
from file_manager import FileManager


def main() -> None:
    # Load configuration
    try:
        config = Config.load()
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERRORE] Configurazione: {e}")
        sys.exit(1)

    logger = ProcessingLogger(config.log_dir)
    logger.info("=== Avvio processamento FSE ===")

    # Connect to IMAP
    email_client = EmailClient(config, logger)
    try:
        email_client.connect()
    except Exception as e:
        logger.error(f"Connessione IMAP fallita: {e}")
        sys.exit(1)

    # Fetch unread emails
    try:
        emails = email_client.fetch_unread_emails()
    except Exception as e:
        logger.error(f"Errore fetch email: {e}")
        email_client.disconnect()
        sys.exit(1)

    logger.emails_found = len(emails)
    if not emails:
        logger.info("Nessuna email da processare")
        email_client.disconnect()
        logger.save_summary()
        return

    # Start browser
    file_manager = FileManager(config, logger)
    browser = FSEBrowser(config, logger)
    try:
        browser.start()
    except Exception as e:
        logger.error(f"Avvio browser fallito: {e}")
        email_client.disconnect()
        sys.exit(1)

    # Process each email
    try:
        for email_data in emails:
            logger.info(
                f"--- Processo email: {email_data.patient_name} "
                f"(CF: {email_data.codice_fiscale}) ---"
            )

            # Navigate FSE and get documents
            doc_results = browser.process_patient(email_data.fse_link, email_data.patient_name)

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

            # Mark email as read only if all documents processed successfully
            if all_ok:
                try:
                    email_client.mark_as_read(email_data.uid)
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

    # Save mapping and summary
    file_manager.save_mappings()
    logger.save_summary()
    logger.info("=== Processamento completato ===")


if __name__ == "__main__":
    main()
