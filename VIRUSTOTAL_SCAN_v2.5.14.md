# Scansione VirusTotal - FSE Processor v2.5.14

**Data scansione:** 2026-03-28 06:01 UTC
**File:** `FSE_Processor_Setup_2.5.14_QT6.exe`
**SHA256:** `7b305500a744dae00726117b84a19e11b963818b04bb88930fcf0e32d5c11631`
**Link VirusTotal:** [7b305500a744dae0...](https://www.virustotal.com/gui/file/7b305500a744dae00726117b84a19e11b963818b04bb88930fcf0e32d5c11631)

## Risultati

| Metrica | Valore |
|---------|--------|
| Motori totali | 75 |
| Nessun rilevamento | 67 |
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
