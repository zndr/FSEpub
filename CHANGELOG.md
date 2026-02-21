# Changelog

## [1.1.0] - 2026-02-21

### Aggiunto

- Guida utente HTML standalone (`guida_utente.html`) con navigazione, sezioni collassabili e layout responsive
- Pulsante "Guida" nel Tab SISS per aprire la guida nel browser
- Tab Paziente: download referti per singolo paziente tramite codice fiscale
- Tab Paziente: filtri per ente/struttura e periodo (settimana, mese, anno, personalizzato)
- Tab Paziente: selezione gerarchica tipologie documento (Lab, Imaging, Anat. Pat., Specialistica, Dimissione, PS)
- Pulsante "Test connessione" nelle impostazioni Server Posta
- Tooltip descrittivi su tutti i controlli tecnici
- Sezione troubleshooting per conflitto porta CDP nella guida utente
- Guida inclusa nel bundle PyInstaller e nell'installer

### Modificato

- Porta CDP rimossa dalla UI (resta configurabile in `settings.env` per utenti avanzati)
- Layout impostazioni riorganizzato in due colonne

## [1.0.0] - 2026-02-21

Prima release di FSE Processor.

### Funzionalita

- Connessione POP3 per recupero email con referti FSE
- Automazione browser (Playwright) per download documenti dal portale FSE
- Rinomina automatica PDF con nome paziente e codice fiscale
- GUI Tkinter con interfaccia a schede (Integrazione SISS / Impostazioni)
- Rilevamento automatico browser installati (Edge, Chrome, Firefox, Brave)
- Rilevamento automatico lettori PDF dal registro di Windows
- Supporto Chrome DevTools Protocol (CDP) per sessione browser esistente
- Modalita installata (Program Files + AppData) e portatile
- Installer Inno Setup con supporto italiano/inglese
- Logging strutturato con statistiche di sessione
- Tracciamento UID email processate per evitare duplicati

---

Tutte le modifiche importanti a questo progetto saranno documentate in questo file.

Il formato e basato su [Keep a Changelog](https://keepachangelog.com/it/1.0.0/),
e questo progetto aderisce al [Semantic Versioning](https://semver.org/lang/it/).
