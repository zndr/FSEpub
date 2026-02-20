import threading
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

from config import Config
from logger_module import ProcessingLogger

FSE_BASE_URL = "https://operatorisiss.servizirl.it/opefseie/"

TIPOLOGIA_VALIDE_ESATTE = {"LETTERA DI DIMISSIONE", "VERBALE PRONTO SOCCORSO"}


def _is_tipologia_valida(tipologia: str) -> bool:
    upper = tipologia.strip().upper()
    if upper.startswith("REFERTO"):
        return True
    return upper in TIPOLOGIA_VALIDE_ESATTE


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
        channel = self._config.browser_channel

        if channel == "firefox":
            self._context = self._playwright.firefox.launch_persistent_context(
                user_data_dir=str(self._config.browser_data_dir),
                headless=self._config.headless,
                accept_downloads=True,
            )
        elif channel == "chromium":
            # Bundled Chromium (no channel) - auto-download if needed
            try:
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self._config.browser_data_dir),
                    headless=self._config.headless,
                    accept_downloads=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                if "Executable doesn't exist" in str(e):
                    self._logger.info("Chromium non trovato, avvio download automatico...")
                    import subprocess, sys
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                    self._context = self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self._config.browser_data_dir),
                        headless=self._config.headless,
                        accept_downloads=True,
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                else:
                    raise
        elif channel in ("chrome", "msedge"):
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._config.browser_data_dir),
                headless=self._config.headless,
                accept_downloads=True,
                channel=channel,
                args=["--disable-blink-features=AutomationControlled"],
            )
        else:
            # Custom executable path (e.g. Brave)
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._config.browser_data_dir),
                headless=self._config.headless,
                accept_downloads=True,
                executable_path=channel,
                args=["--disable-blink-features=AutomationControlled"],
            )

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(self._config.page_timeout)
        self._logger.info(f"Browser avviato (channel={channel}, headless={self._config.headless})")

    def _restart(self) -> None:
        """Restart browser after a crash, preserving the persistent session."""
        self._logger.info("Riavvio browser...")
        self.stop()
        self.start()

    def _is_alive(self) -> bool:
        """Check if browser/page is still usable."""
        try:
            self._page.title()
            return True
        except Exception:
            return False

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

    def wait_for_manual_login(self) -> None:
        """Navigate to the FSE portal and wait for the user to complete SSO login."""
        import time

        self._logger.info("Navigazione al portale FSE per login manuale...")
        self._page.goto(FSE_BASE_URL, wait_until="networkidle")

        # Try clicking "Accedi" to trigger SSO redirect
        try:
            accedi = self._page.get_by_role("button", name="Accedi")
            if accedi.is_visible(timeout=5000):
                accedi.click()
                self._page.wait_for_load_state("networkidle")
        except Exception:
            pass  # Button might not be there, that's ok

        # If we're on the SSO page, wait for the user to complete login
        self._logger.info(
            "LOGIN MANUALE RICHIESTO - Completa l'accesso nel browser. "
            "L'app proseguira' automaticamente dopo il login."
        )

        # Poll until we're back on the FSE portal (not on the SSO/login page)
        max_wait = 300  # 5 minutes max
        elapsed = 0
        while elapsed < max_wait:
            try:
                # Check all pages using JS to get the real URL (page.url can be stale)
                for page in self._context.pages:
                    try:
                        real_url = page.evaluate("window.location.href")
                    except Exception:
                        real_url = page.url
                    if "operatorisiss" in real_url and "idpcrlmain" not in real_url and "ssoauth" not in real_url:
                        self._page = page
                        self._page.set_default_timeout(self._config.page_timeout)
                        self._logger.info(f"Login rilevato su: {real_url}")
                        break
                else:
                    # Not found yet - every 30s try navigating to FSE as fallback
                    if elapsed > 0 and elapsed % 30 == 0:
                        self._logger.info(f"Poll login [{elapsed}s] - tentativo navigazione diretta FSE...")
                        try:
                            self._page.goto(FSE_BASE_URL, wait_until="networkidle", timeout=10000)
                            real_url = self._page.evaluate("window.location.href")
                            if "operatorisiss" in real_url and "idpcrlmain" not in real_url:
                                self._logger.info(f"Login rilevato dopo navigazione diretta: {real_url}")
                                break
                        except Exception:
                            pass
                    time.sleep(2)
                    elapsed += 2
                    continue
                break  # Found it
            except Exception as e:
                self._logger.info(f"Poll login [{elapsed}s] - errore: {e}")
                time.sleep(2)
                elapsed += 2

        if elapsed >= max_wait:
            raise RuntimeError("Timeout attesa login manuale (5 minuti)")

        self._page.wait_for_load_state("networkidle")
        self._logger.info("Login manuale completato, proseguo con l'automazione")

    def _fill_codice_fiscale(self, codice_fiscale: str) -> None:
        """Fill the codice fiscale field and submit the search."""
        cf_input = self._page.locator("#inputcf")
        cf_input.fill(codice_fiscale)
        self._logger.info(f"Codice fiscale inserito: {codice_fiscale}")
        # Click "Cerca" button to submit
        self._page.get_by_role("button", name="Cerca").click()
        self._page.wait_for_load_state("networkidle")
        # Click "Accedi" button to enter the patient's FSE
        self._page.locator("button.btn-xlarge").click()
        self._page.wait_for_load_state("networkidle")
        # Click "Referti" link
        self._page.locator("span", has_text="Referti").click()
        self._page.wait_for_load_state("networkidle")

    def process_patient(self, fse_link: str, patient_name: str, codice_fiscale: str,
                        stop_event: threading.Event | None = None) -> list[DocumentResult]:
        # Check stop request
        if stop_event is not None and stop_event.is_set():
            self._logger.info(f"Processamento interrotto prima di {patient_name}")
            return []

        # Restart browser if it crashed
        if not self._is_alive():
            self._restart()

        self._logger.info(f"Navigazione FSE per {patient_name}: {fse_link}")
        results: list[DocumentResult] = []

        try:
            self._page.goto(fse_link, wait_until="networkidle")

            # Check if we need to click "Accedi" (session might already be active)
            accedi = self._page.get_by_role("button", name="Accedi")
            if accedi.is_visible(timeout=3000):
                accedi.click()
                self._page.wait_for_load_state("networkidle")

            # Check if we got redirected to SSO (session expired)
            if "idpcrlmain" in self._page.url or "ssoauth" in self._page.url:
                import time
                self._logger.warning(
                    "Sessione SSO scaduta - completa il login nel browser. "
                    "L'app proseguira' automaticamente."
                )
                max_wait = 60
                elapsed = 0
                while elapsed < max_wait:
                    try:
                        current_url = self._page.url
                        if "operatorisiss" in current_url and "idpcrlmain" not in current_url:
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                    elapsed += 2
                if elapsed >= max_wait:
                    raise RuntimeError("Timeout attesa re-login (1 minuto)")

                self._page.wait_for_load_state("networkidle")
                # After re-login, navigate again to the patient
                self._page.goto(fse_link, wait_until="networkidle")
                accedi = self._page.get_by_role("button", name="Accedi")
                if accedi.is_visible(timeout=3000):
                    accedi.click()
                    self._page.wait_for_load_state("networkidle")

            # Fill the patient's codice fiscale, click Cerca, Accedi, Referti
            self._fill_codice_fiscale(codice_fiscale)

            # Wait for table to appear
            self._page.wait_for_selector("table tbody tr", state="attached")

        except Exception as e:
            self._logger.error(f"Errore navigazione FSE per {patient_name}: {e}")
            self._take_debug_screenshot(patient_name)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        # Find the referti table (the one containing "Tipologia documento" header)
        try:
            referti_table = self._page.locator("table:has(th:has-text('Tipologia documento'))")
            referti_table.wait_for(state="attached", timeout=10000)

            headers = referti_table.locator("thead th")
            header_count = headers.count()
            header_texts = [headers.nth(j).inner_text().strip() for j in range(header_count)]
            self._logger.info(f"Intestazioni tabella referti: {header_texts}")

            date_col = tipo_col = visualizza_col = None
            for idx, h in enumerate(header_texts):
                h_upper = h.upper()
                if "DATA" in h_upper and date_col is None:
                    date_col = idx
                elif "TIPOLOGIA" in h_upper:
                    tipo_col = idx
                elif "VISUALIZZA" in h_upper:
                    visualizza_col = idx

            if tipo_col is None:
                raise RuntimeError(f"Colonna 'Tipologia' non trovata nelle intestazioni: {header_texts}")

            # Select only data rows from the referti table
            data_rows = referti_table.locator("tbody tr:has(td)")
            row_count = data_rows.count()
            self._logger.info(f"Trovate {row_count} righe dati nella tabella referti")

            if row_count == 0:
                return results

            # Debug: log first data row
            first_row_html = data_rows.nth(0).inner_html()
            self._logger.info(f"HTML prima riga dati: {first_row_html[:500]}")

            # Extract data from data rows
            row_data: list[tuple[int, str, str]] = []
            for i in range(row_count):
                cells = data_rows.nth(i).locator("td")
                cell_count = cells.count()
                if cell_count <= max(date_col or 0, tipo_col or 0):
                    continue  # Row doesn't have enough cells
                date_text = cells.nth(date_col).inner_text().strip() if date_col is not None else ""
                tipo_text = cells.nth(tipo_col).inner_text().strip()
                row_data.append((i, date_text, tipo_text))

            if not row_data:
                self._logger.warning("Nessuna riga dati valida trovata")
                return results

            # Log first few rows for debugging
            for row_idx, date_text, tipo_text in row_data[:5]:
                self._logger.info(f"Riga {row_idx + 1}: data='{date_text}', tipologia='{tipo_text}'")

            # Most recent date = first row (table is sorted newest first)
            most_recent_date = row_data[0][1]
            self._logger.info(f"Data piu' recente: {most_recent_date}")

            # Filter: same date + valid tipologia
            for row_idx, date_text, tipo_text in row_data:
                if date_text != most_recent_date:
                    self._logger.info(f"Riga {row_idx + 1}: data '{date_text}' diversa, stop scansione")
                    break

                if not _is_tipologia_valida(tipo_text):
                    self._logger.info(f"Riga {row_idx + 1}: tipologia '{tipo_text}' non di interesse, saltata")
                    results.append(DocumentResult(
                        disciplina=tipo_text, skipped=True, download_path=None, error=None
                    ))
                    continue

                # Download document
                result = self._download_document(row_idx, tipo_text, patient_name, visualizza_col, data_rows)
                results.append(result)

        except Exception as e:
            self._logger.error(f"Errore lettura tabella per {patient_name}: {e}")
            self._take_debug_screenshot(patient_name)
            return [DocumentResult(disciplina="N/A", skipped=False, download_path=None, error=str(e))]

        return results

    def _download_document(self, row_index: int, tipologia: str, patient_name: str,
                           visualizza_col: int | None, data_rows=None) -> DocumentResult:
        for attempt in range(2):  # max 1 retry
            try:
                if data_rows is not None:
                    row = data_rows.nth(row_index)
                else:
                    row = self._page.locator("table tbody tr:has(td)").nth(row_index)

                # Click the icon/link in the Visualizza column
                if visualizza_col is not None:
                    visualizza_cell = row.locator("td").nth(visualizza_col)
                else:
                    visualizza_cell = row.locator("td").last

                # Debug: log what's in the cell
                cell_html = visualizza_cell.inner_html()
                self._logger.info(f"HTML cella Visualizza: {cell_html[:200]}")

                # Click the first clickable element in the cell
                clickable = visualizza_cell.locator("a, button, img, svg, i, span[class*='icon'], span[class*='glyph']").first
                if clickable.count() == 0:
                    # Fallback: click the cell itself
                    clickable = visualizza_cell

                with self._page.expect_download(timeout=self._config.download_timeout) as download_info:
                    clickable.click()
                    # Wait for Accetta button and click it if it appears
                    accetta = self._page.get_by_role("button", name="Accetta")
                    if accetta.is_visible(timeout=5000):
                        accetta.click()

                download = download_info.value
                # Generate unique filename to avoid overwrites
                ext = Path(download.suggested_filename).suffix or ".pdf"
                unique_name = f"{patient_name}_{row_index}{ext}".replace(" ", "_")
                save_path = self._config.download_dir / unique_name
                download.save_as(str(save_path))

                self._logger.info(
                    f"Download completato: {save_path.name} (tipologia: {tipologia})"
                )
                return DocumentResult(
                    disciplina=tipologia, skipped=False, download_path=save_path, error=None
                )

            except Exception as e:
                if attempt == 0:
                    self._logger.warning(
                        f"Download fallito per riga {row_index + 1} ({tipologia}), "
                        f"tentativo {attempt + 1}/2: {e}"
                    )
                    try:
                        self._page.reload(wait_until="networkidle")
                        self._page.wait_for_selector("table tbody tr", state="visible")
                    except Exception:
                        pass
                else:
                    self._logger.error(
                        f"Download fallito definitivamente per riga {row_index + 1} ({tipologia}): {e}"
                    )
                    self._take_debug_screenshot(f"{patient_name}_row{row_index + 1}")

        return DocumentResult(
            disciplina=tipologia, skipped=False, download_path=None, error="Download fallito dopo retry"
        )

    def _take_debug_screenshot(self, label: str) -> None:
        try:
            screenshot_path = self._config.log_dir / f"debug_{label}.png"
            self._page.screenshot(path=str(screenshot_path))
            self._logger.debug(f"Screenshot debug salvato: {screenshot_path}")
        except Exception:
            pass
