from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

from config import Config
from logger_module import ProcessingLogger

SKIP_DISCIPLINA = {
    "PRESTAZIONI DI LABORATORIO ANALISI CHIMICHE",
    "NON DISPONIBILE",
}


@dataclass
class DocumentResult:
    disciplina: str
    skipped: bool
    download_path: Path | None
    error: str | None


class FSEBrowser:
    def __init__(self, config: Config, logger: ProcessingLogger) -> None:
        self._config = config
        self._logger = logger
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._config.browser_data_dir),
            headless=self._config.headless,
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(self._config.page_timeout)
        self._logger.info(f"Browser avviato (headless={self._config.headless})")

    def stop(self) -> None:
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._context = None
        self._playwright = None
        self._page = None
        self._logger.info("Browser chiuso")

    def process_patient(self, fse_link: str, patient_name: str) -> list[DocumentResult]:
        self._logger.info(f"Navigazione FSE per {patient_name}: {fse_link}")
        results: list[DocumentResult] = []

        try:
            self._page.goto(fse_link, wait_until="networkidle")
            self._page.get_by_role("button", name="Accedi").click()
            self._page.wait_for_load_state("networkidle")
            self._page.get_by_role("tab", name="Referti").click()
            self._page.wait_for_load_state("networkidle")

            # Wait for table to appear
            self._page.wait_for_selector("table tbody tr", state="visible")

        except Exception as e:
            self._logger.error(f"Errore navigazione FSE per {patient_name}: {e}")
            self._take_debug_screenshot(patient_name)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        # Read table rows and extract discipline
        try:
            rows = self._page.locator("table tbody tr")
            row_count = rows.count()
            self._logger.info(f"Trovate {row_count} righe nella tabella referti")
        except Exception as e:
            self._logger.error(f"Errore lettura tabella per {patient_name}: {e}")
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        for i in range(row_count):
            row = rows.nth(i)
            try:
                # Extract disciplina from the row cells
                cells = row.locator("td")
                disciplina = cells.nth(1).inner_text().strip()  # Adjust index if needed
            except Exception:
                disciplina = "UNKNOWN"

            if disciplina.upper() in SKIP_DISCIPLINA:
                self._logger.info(f"Riga {i + 1}: disciplina '{disciplina}' saltata (filtro)")
                results.append(DocumentResult(
                    disciplina=disciplina, skipped=True, download_path=None, error=None
                ))
                continue

            # Download document
            result = self._download_document(i, disciplina, patient_name)
            results.append(result)

        return results

    def _download_document(self, row_index: int, disciplina: str, patient_name: str) -> DocumentResult:
        for attempt in range(2):  # max 1 retry
            try:
                row_selector = f"table tbody tr:nth-child({row_index + 1})"

                with self._page.expect_download(timeout=self._config.download_timeout) as download_info:
                    self._page.locator(row_selector).get_by_text("Visualizza").click()
                    # Wait for Accetta button and click it
                    accetta = self._page.get_by_role("button", name="Accetta")
                    if accetta.is_visible(timeout=5000):
                        accetta.click()

                download = download_info.value
                save_path = self._config.download_dir / download.suggested_filename
                download.save_as(str(save_path))

                self._logger.info(
                    f"Download completato: {save_path.name} (disciplina: {disciplina})"
                )
                return DocumentResult(
                    disciplina=disciplina, skipped=False, download_path=save_path, error=None
                )

            except Exception as e:
                if attempt == 0:
                    self._logger.warning(
                        f"Download fallito per riga {row_index + 1} ({disciplina}), "
                        f"tentativo {attempt + 1}/2: {e}"
                    )
                    try:
                        self._page.reload(wait_until="networkidle")
                        self._page.wait_for_selector("table tbody tr", state="visible")
                    except Exception:
                        pass
                else:
                    self._logger.error(
                        f"Download fallito definitivamente per riga {row_index + 1} ({disciplina}): {e}"
                    )
                    self._take_debug_screenshot(f"{patient_name}_row{row_index + 1}")

        return DocumentResult(
            disciplina=disciplina, skipped=False, download_path=None, error="Download fallito dopo retry"
        )

    def _take_debug_screenshot(self, label: str) -> None:
        try:
            screenshot_path = self._config.log_dir / f"debug_{label}.png"
            self._page.screenshot(path=str(screenshot_path))
            self._logger.debug(f"Screenshot debug salvato: {screenshot_path}")
        except Exception:
            pass
