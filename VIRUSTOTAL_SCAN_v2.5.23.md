# Scansione VirusTotal - FSE Processor v2.5.23

**Data scansione:** 2026-06-01 12:42 UTC
**File:** `FSE_Processor_Setup_2.5.23_QT6.exe`
**SHA256:** `c05b6314d958828d398c6859ddb0c5ef081e482006ec4beb54226a0ba807d39e`
**Link VirusTotal:** [c05b6314d958828d...](https://www.virustotal.com/gui/file/c05b6314d958828d398c6859ddb0c5ef081e482006ec4beb54226a0ba807d39e)

## Risultati

| Metrica | Valore |
|---------|--------|
| Motori totali | 75 |
| Nessun rilevamento | 64 |
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
