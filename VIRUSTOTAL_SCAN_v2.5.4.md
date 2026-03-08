# Scansione VirusTotal - FSE Processor v2.5.4

**Data scansione:** 2026-03-08 19:13 UTC
**File:** `FSE_Processor_Setup_2.5.4_QT6.exe`
**SHA256:** `c3238e92d1a10c8c7fabb1049970cb6e7ee7b258909693278f64985d543d0b17`
**Link VirusTotal:** [c3238e92d1a10c8c...](https://www.virustotal.com/gui/file/c3238e92d1a10c8c7fabb1049970cb6e7ee7b258909693278f64985d543d0b17)

## Risultati

| Metrica | Valore |
|---------|--------|
| Motori totali | 76 |
| Nessun rilevamento | 58 |
| Rilevamenti (malicious/suspicious) | 1 |

### Dettaglio rilevamenti

| Motore | Rilevamento |
|--------|-------------|
| DeepInstinct | MALICIOUS |

## Nota sui falsi positivi

I rilevamenti sopra indicati sono con alta probabilita' **falsi positivi**.

FSE Processor e' compilato con [PyInstaller](https://pyinstaller.org/), il cui bootloader e' notoriamente segnalato da alcuni motori antivirus basati su euristica/AI. Questo e' un problema noto e documentato:

- [PyInstaller FAQ: falsi positivi](https://pyinstaller.org/en/stable/when-things-go-wrong.html#false-positives)
- [VirusTotal Blog: packed executables](https://blog.virustotal.com/2023/01/packed-executables.html)

**Motori noti per FP su PyInstaller:** DeepInstinct

Il codice sorgente di FSE Processor e' disponibile per verifica nel repository privato. L'hash SHA256 dell'installer corrisponde a quello pubblicato in `version.json`.
