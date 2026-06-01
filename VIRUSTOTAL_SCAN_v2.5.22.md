# Scansione VirusTotal - FSE Processor v2.5.22

**Data scansione:** 2026-06-01 05:03 UTC
**File:** `FSE_Processor_Setup_2.5.22_QT6.exe`
**SHA256:** `61e08db2c1a7bf5a59634326c99b87c59a5687b57818299fcef5a5243601dec6`
**Link VirusTotal:** [61e08db2c1a7bf5a...](https://www.virustotal.com/gui/file/61e08db2c1a7bf5a59634326c99b87c59a5687b57818299fcef5a5243601dec6)

## Risultati

| Metrica | Valore |
|---------|--------|
| Motori totali | 75 |
| Nessun rilevamento | 68 |
| Rilevamenti (malicious/suspicious) | 1 |

### Dettaglio rilevamenti

| Motore | Rilevamento |
|--------|-------------|
| Gridinsoft | Trojan.Win32.Downloader.oa!s1 |

## Nota sui falsi positivi

I rilevamenti sopra indicati sono con alta probabilita' **falsi positivi**.

FSE Processor e' compilato con [PyInstaller](https://pyinstaller.org/), il cui bootloader e' notoriamente segnalato da alcuni motori antivirus basati su euristica/AI. Questo e' un problema noto e documentato:

- [PyInstaller FAQ: falsi positivi](https://pyinstaller.org/en/stable/when-things-go-wrong.html#false-positives)
- [VirusTotal Blog: packed executables](https://blog.virustotal.com/2023/01/packed-executables.html)

**Motori noti per FP su PyInstaller:** Gridinsoft

Il codice sorgente di FSE Processor e' disponibile per verifica nel repository privato. L'hash SHA256 dell'installer corrisponde a quello pubblicato in `version.json`.
