# Changelog

## [2.3.4] - 2026-02-23

### Corretto

- Email non marcate come lette dopo il download: aggiunto auto-reconnect IMAP prima di impostare il flag `\Seen` (la connessione poteva scadere durante il login SSO manuale)
- Usato `BODY.PEEK[]` al posto di `RFC822` nel fetch per non impostare `\Seen` prematuramente
- Testo console invisibile su Windows con tema scuro: aggiunto colore testo esplicito a tutti i widget per sovrascrivere la palette di sistema

## [2.2.1] - 2026-02-22

### Aggiunto

- Controllo automatico aggiornamenti all'avvio: l'app verifica silenziosamente la disponibilita di nuove versioni 2 secondi dopo l'apertura della finestra
- Se disponibile un aggiornamento, l'utente viene avvisato con dialog e possibilita di scaricare direttamente
- Nessuna interruzione se la versione e gia aggiornata o se la rete non e disponibile

### Modificato

- Refactoring metodo `_check_updates()` con parametro `silent` per distinguere il controllo automatico (silenzioso) da quello manuale (menu Aiuto)

## [2.2.0] - 2026-02-22

### Aggiunto

- Crittografia credenziali email: la password viene salvata crittografata in `settings.env` con prefisso `ENC:` (Fernet + PBKDF2HMAC legato all'identita Windows)
- Dialog dedicato "Cambia password" con verifica della password attuale, nuova password e conferma
- Migrazione automatica: le password in chiaro vengono crittografate al primo avvio senza intervento dell'utente
- Nuovo modulo `credential_manager.py` per gestione centralizzata delle credenziali
- Dipendenza `cryptography>=42.0.0`

### Modificato

- Campo password email reso read-only con pulsante "Cambia..." a fianco
- `config.py` decritta automaticamente `EMAIL_PASS` al caricamento


## [2.1.0] - 2026-02-22

### Aggiunto

- Tema blu professionale con barra dei menu
- Pulsante "Carica strutture" per pre-caricare il dropdown Ente prima del download
- Pulsante "Interrompi" per fermare il ciclo di attesa login
- Dialogo di conferma al salvataggio delle impostazioni
- Suffisso _QT6 al nome file installer

### Modificato

- Checkbox sotto-tipo referto compattate in riga singola con label brevi e tooltip
- Tab paziente migliorato: placeholder Ente, feedback progresso, riepilogo e pulsanti di pulizia
- Migliorata visibilita e gestione finestra browser durante l'automazione
- Messaggio di fallback migliorato quando il browser di sistema non e disponibile

### Corretto

- Fix connessione CDP: validazione endpoint con HTTP, timeout aumentato e retry su sessione stale

## [2.0.0] - 2026-02-21

### Modificato

- Migrazione completa interfaccia grafica da Tkinter a PySide6 (Qt6)
- Look nativo Windows moderno con widget Qt6
- Tooltip nativi Qt al posto della classe Tooltip custom
- Sistema threading riscritto con Qt Signal/Slot e QTimer al posto di `.after()`
- Dialoghi nativi Qt (QMessageBox, QFileDialog, QDialog)
- Layout con QVBoxLayout/QHBoxLayout/QGridLayout al posto di pack/grid Tkinter

### Rimosso

- Dipendenza da Tkinter
- Classe Tooltip custom (sostituita da setToolTip nativo)
- StringVar/BooleanVar (sostituiti da accesso diretto ai widget)

### Aggiunto

- Dipendenza PySide6 >= 6.6.0
- Classe _SignalBridge per comunicazione thread-safe tra worker e GUI

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
