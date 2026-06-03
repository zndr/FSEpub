# Scansione VirusTotal - FSE Processor v2.5.24

**Data scansione:** 2026-06-03 04:00 UTC
**File:** `FSE_Processor_Setup_2.5.24_QT6.exe`
**SHA256:** `f09d585ff566444bc06197398331312e41561b21e880fd6325a2e6cd13595596`
**Link VirusTotal:** [f09d585ff566444b...](https://www.virustotal.com/gui/file/f09d585ff566444bc06197398331312e41561b21e880fd6325a2e6cd13595596)

## Risultati

| Metrica | Valore |
|---------|--------|
| Motori totali | 75 |
| Nessun rilevamento | 65 |
| Rilevamenti (malicious/suspicious) | 2 |

### Dettaglio rilevamenti

| Motore | Rilevamento |
|--------|-------------|
| DeepInstinct | MALICIOUS |
| Gridinsoft | Trojan.Win32.Downloader.oa!s1 |

## Nota sui falsi positivi

I rilevamenti sopra indicati sono con alta probabilita' **falsi positivi**.

FSE Processor e' compilato con [PyInstaller](https://pyinstaller.org/), il cui bootloader e' notoriamente segnalato da alcuni motori antivirus basati su euristica/AI. Questo e' un problema noto e documentato:

- [PyInstaller FAQ: falsi positivi](https://pyinstaller.org/en/stable/when-things-go-wrong.html#false-positives)
- [VirusTotal Blog: packed executables](https://blog.virustotal.com/2023/01/packed-executables.html)

**Motori noti per FP su PyInstaller:** DeepInstinct, Gridinsoft

Il codice sorgente di FSE Processor e' disponibile per verifica nel repository privato. L'hash SHA256 dell'installer corrisponde a quello pubblicato in `version.json`.
