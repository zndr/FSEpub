# FSE Processor

Applicazione desktop Windows per il download automatico dei referti medici dal portale **FSE** (Fascicolo Sanitario Elettronico) della Regione Lombardia.

Recupera le notifiche email dei nuovi referti, apre il portale FSE in un browser automatizzato, scarica i PDF e li rinomina con il nome del paziente e il codice fiscale. Opzionalmente estrae il testo dai referti, anonimizza i dati personali e produce un'analisi strutturata tramite intelligenza artificiale.

## Funzionalita

- **Monitoraggio email** - Connessione IMAP per recuperare le notifiche di nuovi referti FSE
- **Automazione browser** - Download automatico dei PDF dal portale FSE tramite Playwright (supporto CDP per sessione browser esistente)
- **Rinomina intelligente** - I PDF vengono rinominati nel formato `CF_NOME_COGNOME_TIPO.pdf`
- **Estrazione testo** - Estrazione del contenuto testuale dai PDF con pdfplumber (layout semplice, strutturato, tabelle)
- **Anonimizzazione** - Rimozione automatica dei dati personali (nome, CF, indirizzo, ecc.) con 15 profili documento specifici per struttura sanitaria
- **Analisi AI** - Interpretazione strutturata dei referti tramite LLM (Claude, ChatGPT, Gemini, Mistral, Claude CLI, endpoint personalizzato)
- **Ricerca per paziente** - Download referti per singolo paziente con filtri per ente, tipologia e periodo
- **Integrazione Millewin** - Auto-polling per rilevamento cambio paziente nel gestionale medico
- **Wizard configurazione** - Procedura guidata in 7 step per la prima installazione
- **Aggiornamento automatico** - Controllo nuove versioni all'avvio con download diretto
- **Crittografia credenziali** - Password email salvata crittografata (Fernet + PBKDF2HMAC)

## Requisiti

- **Windows 10/11** (64-bit)
- **Python 3.10+**
- Browser basato su Chromium (Edge, Chrome, Brave) per la modalita CDP, oppure Chromium viene installato automaticamente da Playwright

## Installazione

### Installer (consigliato)

Scarica l'installer dalla pagina [Releases](https://github.com/zndr/FSEpub/releases/latest) ed esegui il setup.

### Da sorgente

```bash
git clone https://github.com/zndr/FSEpub.git
cd FSEpub
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

### Dipendenze

```
playwright==1.50.0
python-dotenv==1.1.0
PySide6>=6.6.0
pillow
cryptography>=42.0.0
pdfplumber>=0.11.0
httpx>=0.27.0
```

## Utilizzo

### Modalita standard

1. Avvia l'applicazione
2. Al primo avvio si apre il **wizard di configurazione** (oppure da menu *Aiuto > Configurazione guidata*)
3. Configura account email IMAP, cartelle e parametri browser
4. Nella scheda **Integrazione SISS**, clicca **Avvia** per iniziare il download automatico dei referti

### Modalita CDP (consigliata)

La modalita CDP (Chrome DevTools Protocol) permette di usare una sessione browser gia autenticata, evitando di ripetere il login SSO:

1. Abilita CDP nelle impostazioni (o tramite il wizard)
2. Avvia il browser con il flag `--remote-debugging-port=9222`
3. Effettua il login al portale FSE manualmente
4. Avvia il processamento: l'automazione riutilizza la sessione esistente

### Ricerca per paziente

Nella scheda **Paziente**, inserisci il codice fiscale e seleziona i filtri desiderati (ente, tipologia documento, periodo) per scaricare i referti di un singolo paziente.

### Analisi AI dei referti

Il sistema supporta 6 provider per l'analisi AI:

| Provider | Richiede API Key | Note |
|---|---|---|
| Claude (Anthropic) | Si | API a consumo |
| ChatGPT (OpenAI) | Si | API a consumo |
| Gemini (Google) | Si | API a consumo |
| Mistral | Si | API a consumo |
| **Claude CLI (locale)** | **No** | **Usa l'abbonamento Claude Pro/Max esistente** |
| Endpoint personalizzato | Opzionale | Qualsiasi endpoint compatibile OpenAI |

> **Suggerimento:** se hai un abbonamento Claude Pro o Max, seleziona **Claude CLI (locale)** per usare l'AI senza costi aggiuntivi. Richiede solo che [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) sia installato.

## Struttura del progetto

```
FSE Processor/
├── main.py                  # Entry point e logica di processamento
├── gui.py                   # Interfaccia grafica PySide6 (Qt6)
├── browser_automation.py    # Automazione browser Playwright + CDP
├── email_client.py          # Client IMAP per recupero email
├── file_manager.py          # Gestione file e rinomina PDF
├── config.py                # Caricamento configurazione da settings.env
├── credential_manager.py    # Crittografia credenziali
├── report_interpreter.py    # Interprete referti con analisi batch
├── version.py               # Versione (source of truth)
├── text_processing/
│   ├── text_processor.py    # Orchestratore: estrazione -> anonimizzazione -> AI
│   ├── pdf_text_extractor.py  # Estrazione testo da PDF (pdfplumber)
│   ├── text_anonymizer.py   # Anonimizzazione PII
│   ├── report_profiles.py   # 15 profili per tipo documento/struttura
│   └── llm_analyzer.py      # Integrazione multi-provider LLM
├── guida_utente.html        # Guida utente HTML standalone
├── installer.iss            # Script Inno Setup per installer Windows
├── build.bat                # Script di build PyInstaller
└── requirements.txt
```

## Profili documento supportati

L'anonimizzazione e l'estrazione testo sono ottimizzati per 15 profili specifici:

- **PS** - Verbale Pronto Soccorso (ASST Spedali Civili)
- **DIMOSP** - Lettera di Dimissione (ASST, Maugeri)
- **SPEC** - Referti specialistici (ASST, Maugeri Cardiologia, Radiologia, Pneumologia, Richiedei, Centri radiologici)
- **LAB** - Referti di laboratorio (SYNLAB, Bianalisi)
- **Anatomia Patologica** - Referti istologici
- **Default** - Profilo generico per documenti non riconosciuti

## Build

### Eseguibile standalone

```bash
build.bat
```

Genera l'eseguibile nella cartella `dist/` tramite PyInstaller.

### Installer

Richiede [Inno Setup 6](https://jrsoftware.org/isinfo.php):

```bash
iscc installer.iss
```

L'installer viene generato in `installer_output/`.

## Licenza

Questo progetto e distribuito con licenza MIT. Vedi il file [LICENSE](LICENSE) per i dettagli.
