# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FSE Processor — Windows desktop app for automated download and processing of medical reports from Italy's FSE (Fascicolo Sanitario Elettronico) portal. Written in Python 3.10+ with PySide6 GUI and Playwright browser automation.

All UI, comments, logs, and user-facing strings are in **Italian**.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
python -m playwright install chromium

# Run (launches GUI)
python main.py

# Build standalone executable (PyInstaller)
build.bat                  # outputs to dist/

# Build Windows installer (requires Inno Setup 6)
iscc installer.iss         # outputs to installer_output/

# Bump version (updates version.py, the single source of truth)
python bump_version.py
```

There is no test suite. Manual testing uses 53 example PDFs in `esempi/`.

## Architecture

**Pipeline:** Email notifications → Browser automation (FSE portal) → PDF download → Rename → Text extraction → Anonymization → AI analysis

### Key modules

| Module | Role |
|---|---|
| `main.py` | Entry point, CLI processing orchestration |
| `gui.py` (~4700 lines) | PySide6 GUI: 7-step wizard, tabbed interface (SISS, Patient Search, Settings, Logs), threading for async tasks |
| `browser_automation.py` | Playwright automation + CDP session reuse to avoid repeated SSO login |
| `email_client.py` | IMAP client for FSE notification retrieval |
| `file_manager.py` | PDF renaming to `{CF}_{NOME}_{COGNOME}_{TYPE}.pdf` |
| `config.py` | Loads settings from `settings.env` via python-dotenv |
| `credential_manager.py` | Fernet + PBKDF2HMAC encryption for stored passwords |
| `report_interpreter.py` | Batch report analysis and interpretation |
| `app_paths.py` | Path resolution: portable mode (relative) vs installed mode (`%APPDATA%`) |

### Text processing pipeline (`text_processing/`)

| Module | Role |
|---|---|
| `text_processor.py` | Orchestrator: extract → anonymize → (optional) AI analyze |
| `pdf_text_extractor.py` | pdfplumber extraction (simple, layout, table modes) |
| `text_anonymizer.py` | PII removal with dynamic patient-name exclusion |
| `report_profiles.py` (~50K lines) | 15 document profiles with per-provider exclude/keep patterns and identifier regexes |
| `llm_analyzer.py` | Multi-provider LLM: Claude, OpenAI, Gemini, Mistral, Claude CLI, custom OpenAI-compatible endpoint |

### Document type detection

`_detect_doc_type()` in `text_processor.py` reads the TYPE suffix from the filename (`SPEC`, `PS`, `DIMOSP`, `LAB`). Profile matching within each type uses identifier patterns defined in `report_profiles.py`. LAB reports use table extraction; others use simple extraction.

### CDP (Chrome DevTools Protocol) mode

The preferred runtime mode. Reuses an already-authenticated browser session via `--remote-debugging-port`, avoiding SSO re-login. Uses Windows registry to configure the debugging port flag on the user's default browser.

### Portable vs installed mode

`app_paths.py` checks for a `.installed` marker file or `Program Files` in the path. Installed mode puts data in `%APPDATA%\FSE Processor\` and downloads in `Documents\FSE Downloads\`.

## Version management

Single source of truth: `version.py` (`__version__ = "X.Y.Z"`). The PowerShell script `Update-Version.ps1` propagates the version to `installer.iss`, `build.bat`, and other references.

## Configuration

All settings live in `settings.env` (loaded by `config.py`). Key groups: IMAP credentials, file paths, browser/CDP settings, text processing options, LLM provider configuration.
