---
name: medical-report-extractor
description: >
  Estrazione strutturata del corpo testuale da referti medici italiani in formato PDF.
  Usare SEMPRE quando l'utente carica referti medici di qualsiasi tipo (lettere di dimissione,
  referti radiologici, ecografie, esami strumentali, referti di laboratorio, anatomia patologica,
  referti cardiologici, endoscopici, manometrici, audiometrici, o qualsiasi altro documento clinico)
  e richiede l'estrazione del testo clinico rilevante. Attivare anche quando l'utente chiede di
  "leggere", "estrarre", "analizzare", "riassumere" un referto medico PDF, o quando carica un PDF
  che appare essere un documento clinico italiano. Gestisce sia PDF con testo selezionabile sia
  PDF da scansione o fotografia tramite OCR.
---

# Medical Report Extractor

Skill per l'estrazione strutturata del corpo testuale clinico da referti medici italiani in PDF.
L'obiettivo è isolare **esclusivamente il testo clinico descrittivo**, rimuovendo ogni elemento
di layout istituzionale, dato anagrafico e metadato amministrativo.

## Architettura della pipeline

```
PDF → [1. Tipo PDF] → [2. Estrazione testo/tabelle]
    → [3. Identificazione profilo documento]
    → [4. Estrazione nome paziente]
    → [5. Filtraggio righe (exclude/keep)]
    → [6. Inserimento struttura (newline)]
    → [7. Normalizzazione e output]
```

Lo script principale è: `scripts/extract_clinical_text.py`

### Dipendenze

```bash
pip install pdfplumber pymupdf --break-system-packages
# Per OCR (opzionale): tesseract-ocr tesseract-ocr-ita poppler-utils
```

---

## Come usare lo script

### Uso da CLI

```bash
# Estrazione base
python3 scripts/extract_clinical_text.py /path/referto.pdf

# Con output JSON dettagliato
python3 scripts/extract_clinical_text.py /path/referto.pdf --json --debug

# Salva su file
python3 scripts/extract_clinical_text.py /path/referto.pdf -o output.txt
```

### Uso da Python (in Claude)

```python
import sys
sys.path.insert(0, "/mnt/skills/user/medical-report-extractor/scripts")
from extract_clinical_text import extract_clinical_text

result = extract_clinical_text("/mnt/user-data/uploads/referto.pdf", debug=True)

# result.clinical_text       → testo clinico pulito
# result.patient_name        → nome paziente estratto (per uso filename, MAI inviato a LLM)
# result.profile_used        → profilo documento applicato
# result.report_date         → data del referto
# result.signing_doctor      → medico firmante
# result.ocr_used            → True se è stato necessario OCR
# result.warnings            → eventuali avvisi
```

---

## Fasi della pipeline in dettaglio

### Fase 1 — Rilevamento tipo PDF

```python
has_selectable_text(pdf_path)  # True se almeno 50 char di testo
```

- **PDF con testo**: usa `pdfplumber` per estrazione diretta
- **PDF grafico** (scansione/foto): pipeline OCR con `PyMuPDF` + `Tesseract`

### Fase 2 — Estrazione testo

Tre modalità di estrazione, scelte in base al tipo di documento:

| Modalità | Quando | Come |
|----------|--------|------|
| **Simple** | Referti, PS, dimissioni, specialistici | `page.extract_text()` — denso, senza spazi layout |
| **Tables** | Referti di laboratorio (LAB) | `page.extract_tables()` con fallback a simple se tabelle vuote |
| **Layout** | Fallback se simple produce testo vuoto | `page.extract_text(layout=True, x_tolerance=3, y_tolerance=3)` |

Il tipo documento viene rilevato dal suffisso del nome file se disponibile
(`_SPEC`, `_PS`, `_DIMOSP`, `_LAB`).

### Fase 3 — Identificazione profilo documento

Il sistema confronta il testo estratto con i **pattern identificativi** di ciascun profilo
(match case-insensitive per sottostringhe). TUTTI i pattern di un profilo devono matchare.
I profili sono testati dal più specifico al più generico.

**Profili disponibili (14 specifici + 1 default):**

| Profilo | Pattern identificativi | Struttura sanitaria |
|---------|----------------------|---------------------|
| `tsa_maugeri` | "ECOCOLORDOPPLER TRONCHI SOVRAORTICI" | Maugeri Lumezzane |
| `rx_Maugeri` | "Istituto Scientifico di Lumezzane" + "Servizio di Diagnostica per Immagini" | Maugeri Lumezzane |
| `spec_asst_radiologia` | "ASST DEGLI SPEDALI CIVILI DI BRESCIA" + "Diagnostica per Immagini" | ASST Brescia — Radiologia |
| `spec_pneumologia` | "PNEUMOLOGIA" + "CAPACIT" | ASST — Pneumologia/PFR |
| `anatpat` | "ANATOMIA PATOLOGICA" | ASST del Garda e altri |
| `spec_centro_radiologico` | "Numero Referto:" + "Codice Paziente:" | Centri radiologici convenzionati |
| `spec_richiedei` | "Richiedei" | Fondazione Richiedei, Gussago |
| `ps_asst` | "VERBALE DI PRONTO SOCCORSO" | ASST Brescia — PS Gardone VT e Spedali Civili |
| `spec_maugeri_cardio` | "Istituto Scientifico di Lumezzane" + "RIABILITAZIONE" | Maugeri — Cardiologia/Pneumologia Riab. |
| `lab_synlab` | "synlab" | SYNLAB Italia |
| `lab_bianalisi` | "Bianalisi" | Bianalisi srl |
| `dimosp_asst` | "Lettera di dimissione" + "ASST" | ASST Brescia — Dimissioni |
| `dimosp_maugeri` | "Istituto Scientifico di Lumezzane" + "dimettiamo in data" | Maugeri — Dimissioni |
| `spec_asst` | "ASST DEGLI SPEDALI CIVILI DI BRESCIA" | ASST Brescia — Referti ambulatoriali |
| `default` | *(nessun pattern — fallback)* | Qualsiasi struttura italiana |

### Fase 4 — Estrazione nome paziente

Il nome del paziente viene estratto per uso interno (generazione filename, esclusione dinamica
dal testo). **Non viene MAI incluso nel testo clinico di output.**

Ogni profilo ha pattern specifici (testati in ordine, primo match vince):

| Formato nel PDF | Esempio | Profilo |
|-----------------|---------|---------|
| `COGNOME*NOME M/F CF` | `BUSI*IVAN M BSUVNI66A30E738W` | ps_asst |
| `Paziente: NOME COGNOME Nosologico:` | `Paziente: ISMAEL KONE Nosologico: 0326000671` | dimosp_asst |
| `Sig.ra NOME, ricoverata` | `Sig.ra SENECI REBECCA, ricoverata` | dimosp_asst |
| `signora COGNOME NOME (F)` | `signora LA IACONA ANTONINA (F)` | dimosp_maugeri |
| `Paziente NOME Codice fiscale` | `Paziente SCARONI CATIA Codice fiscale` | spec_asst |
| `Sig./Sig.ra: Nome Cognome  ID Paziente` | `Sig./Sig.ra: Paderno Gianfranco ID Paziente` | rx_Maugeri |
| `Paziente: NOME Anni:` | `Paziente: AGNESI ATTILIO Anni: 68` | tsa_maugeri, spec_maugeri_cardio |
| `ID. Paziente: NNN Nome Cognome` | `ID. Paziente: 762523 Rossetti Monica` | spec_richiedei |
| `NOME COGNOME Numero Referto:` | `STRAPPARAVA PIERMARIO Numero Referto:` | spec_centro_radiologico |
| `Tipo amministrativo...\nNOME COGNOME` | (riga isolata dopo "Tipo amministrativo") | anatpat |
| Riga isolata all-caps 2-3 parole | `BAZZANI MARIA` (prima di "Nato/a il") | lab_synlab |
| `Provenienza NOME COGNOME` | `Provenienza MULTARI MARIA ROSA` | lab_bianalisi |

**Gestione nomi composti**: il pattern PS supporta `COGNOME*NOME SECONDO TERZO` (es. PEREGO*GIAN FRANCA ADRIANA).

**Esclusione dinamica**: dopo l'estrazione, il nome viene aggiunto automaticamente ai pattern
di esclusione in entrambi gli ordini (COGNOME NOME e NOME COGNOME).

**Normalizzazione per filename**: `"La Iacona, Antonina"` → `LA_IACONA_ANTONINA`

### Fase 5 — Filtraggio righe

Per ogni riga del testo estratto:
1. Se matcha un **keep pattern** → SEMPRE inclusa (priorità massima)
2. Se matcha un **exclude pattern** → esclusa
3. Altrimenti → inclusa

**Regola fondamentale**: i keep pattern hanno PRIORITÀ SUPERIORE agli exclude pattern.
Se una riga contiene sia un termine da escludere sia contenuto clinico importante,
il keep pattern prevale.

### Fase 6 — Inserimento struttura

Le righe mantenute vengono unite in un testo continuo. I **newline_before_patterns**
inseriscono interruzioni di riga prima delle sezioni logiche del documento per
preservare la struttura.

### Fase 7 — Normalizzazione

- Spazi multipli collassati a singolo spazio
- Tab e return eliminati
- Newline intenzionali preservati
- Spazi finali prima di newline eliminati

---

## Catalogo completo dei pattern

### Pattern di ESCLUSIONE universali (_COMMON_PII)

Applicati da TUTTI i profili:

```
Codice fiscale italiano:   [A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]
Etichetta CF:              Codice [Ff]iscale
Tessera sanitaria:         Tessera Sanitari[ao]
Codice sanitario:          Cod. Sanit.
```

### Pattern di ESCLUSIONE universali (_COMMON_FOOTER)

Footer legali presenti in tutti i documenti sanitari italiani:

| Categoria | Pattern |
|-----------|---------|
| **Firma digitale** | "Documento informatico firmato", "Documento elettronico firmato", "Referto firmato digitalmente ai sensi", "Referto sottoscritto con firma" |
| **Archiviazione** | "stampa costituisce copia", "Archiviato da questo ente", "normativa vigente" |
| **Riferimenti normativi** | D.Lgs. 82/2005, D.P.R. 445/2000, D.P.R. 513, D.C.P.M., D.L.G., D.E. 97/43/EURATOM, D. Min. 14 |
| **Avviso al cittadino** | "IMPORTANTE: il presente referto", "conservato dall'assistito", "ripresentato in caso", "www.crs.lombardia.it", "Fascicolo Sanitario Elettronico", "Servizi on Line per il cittadino" |
| **Metadati stampa** | "ID DOCUMENTO:NNN", "VERSIONE N", "Stampato il :" |
| **Disclaimer clinico** | "consultare il proprio medico di fiducia", "interpretare i risultati", "Si suggerisce di consultare" |

### Pattern di ESCLUSIONE per tipo documento

#### Verbale di Pronto Soccorso (ps_asst)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | DIPARTIMENTO EMERGENZA, ASST DEGLI SPEDALI CIVILI, PRESIDIO OSPEDALIERO, RESPONSABILE DR., VERBALE DI PRONTO SOCCORSO, Cartella Clinica di P.S. |
| **Anagrafica** | Cognome*nome, [COGNOME]*[NOME] M/F CF, Nato a:, Residente a:, Domiciliato a:, Telefono rif:, Cittadinanza:, ASL, ATS DI BRESCIA |
| **Triage** | Modalità d'invio, Codice d'urgenza al triage, Data/Ora di Triage, Data/Ora di chiusura, Data/Ora presa in carico |
| **Footer** | Verbale N°, Versione N°, "Il Medico" (riga isolata), badge medico (es. "LODA IRENE B20247"), luogo+data, "non vale come documentazione ai fini prescrittivi" |

**Keep patterns PS**: ANAMNESI ED ESAME OBIETTIVO, ELENCO PRESTAZIONI, CONSULENZE, DIAGNOSI, TERAPIA, ESITO, PROGNOSI, AN/EO (sigle sezione), termini clinici (Dolore, Trauma, Frattura, FANS)

#### Lettera di Dimissione ASST (dimosp_asst)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | ASST, PRESIDIO OSPEDALIERO, Via Giovanni XXIII, Piazzale Spedali Civili, "Lettera di dimissione" |
| **Sidebar** (layout multi-colonna) | Primario, Coordinatore, Recapiti, numeri telefono 030/, email @asst-spedalicivili.it, nomi isolati (GUIZZI, PORTOLANI), "ortopedia.", "traumatologia.", "gardone@", sigle reparto |
| **Anagrafica** | Nosologico, Nato il, Residente a, Telefono, Regime di ricovero, Paziente interno, Day Hospital |
| **Footer** | Pagina N di M, "NNNNNNNNNN - Versione N", Data Stesura, "Dr. NOME COGNOME" (riga isolata) |

**Keep patterns dimissione**: Diagnosi alla dimissione, Motivo del ricovero, Anamnesi, Decorso clinico, Terapia, Interventi chirurgici, Procedure, Condizioni alla dimissione, Follow-up, Allergie, Muta, Nessuno/a, Regolare, mg, cp, Endovenosa, Si dimette, Sine complicanze

**ATTENZIONE layout multi-colonna**: le lettere di dimissione ASST hanno un sidebar sinistro
con dati del personale che il layout PDF mescola con il testo principale. Pattern come
`^[A-Z]{4,}$` (parola singola uppercase = cognome personale), `[a-z]+@$` (email troncata),
`spedalicivili.it$` intercettano questi residui.

#### Lettera di Dimissione Maugeri (dimosp_maugeri)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header Maugeri** | Istituti Clinici Scientifici Maugeri Spa SB, Via Salvatore Maugeri, C.F. e P.IVA, Iscrizione Rea, Via Mazzini, 25065 Lumezzane, Dirigente Responsabile, Tel/Fax/URP 030, lumezzane@icsmaugeri |
| **Anagrafica** | nato/a il, degente presso, Data di Nascita, Codice Paz. ID, PK-NNN, Indirizzo, Città, Telefono, C.F., Numero Cartella, Tipo: Esterno |
| **Footer** | ICSM SIO DIMI, Firmato da, in data e ora |

#### Referto Specialistico ASST (spec_asst)

Gestisce referti ambulatoriali da molte specialità: diabetologia, oculistica, chirurgia vascolare,
uroginecologia, endoscopia, ortopedia, accessi vascolari, ecc.

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | ASST DEGLI SPEDALI CIVILI, P.O. SPEDALI CIVILI/GARDONE, POLIAMBULATORIO, S.C., Responsabile, Direttore, AMBULATORIO |
| **Anagrafica** | Paziente NOME Codice fiscale, Sig.ra NOME, Nascita GG/MM/AAAA, N. Accesso, Residenza, Tessera Sanitaria, Prestazioni |
| **Footer ASST** | Il Medico Specialista, (Timbro e Firma), Validato/Firmato in Data, NOME (CODICEFISCALE), Brescia GG/MM/AAAA |
| **Radiologia ASST** | Dipartimento di Diagnostica, U.O. Radiologia, CF:, ID Paz, ID Anag.C, *NNNNN* (barcode), Coord. TSRM, Data esame, Medico prescrittore, Quesito diagnostico |

#### Radiologia Maugeri (rx_Maugeri)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | Istituto Scientifico, Servizio di Diagnostica per Immagini, Primario |
| **Anagrafica** | Sig./Sig.ra:, Data di Nascita, Codice Fiscale, ID Paziente, PK-NNN, N. di accesso, ESTERNO |
| **Admin** | Prestazione eseguita, Schedulazione, Esecuzione, Classe dose, TSRM |
| **Footer** | Data validazione, Pag N di M |

#### TSA Maugeri (tsa_maugeri)

Come rx_Maugeri ma con pattern aggiuntivi per la struttura specifica dell'ecocolordoppler:
- Rimozione: Ambulatorio LU, Sesso, Descrizione Esame, note legali post-firma
- Preservazione: CONCLUSIONI, FOLLOW UP, "Referto firmato digitalmente da:"
- Struttura: newline prima di DISTRETTO CAROTIDEO SIN, ARTERIE VERTEBRALI, ARTERIE SUCCLAVIE

#### Cardiologia Maugeri (spec_maugeri_cardio)

Copre: ECO transtoracica, Ecostress, Holter, visite cardiologiche, ECG, prove da sforzo.

Pattern esclusione aggiuntivi: Residenza, Prestazioni richieste, Codice Prestazione Stato,
A4H@ (codici prestazione), EROGATA, Numero Prenotazione, `^_+$` (linee di separazione),
Operatore.

#### Laboratorio SYNLAB (lab_synlab)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | www.synlab.it, customerservice, SYNLAB, Indirizzo, ESTERNO SSN, Codice Lab, Id Referto, Data Referto/Prelievo |
| **Anagrafica** | Nato/a il, Sesso: M/F, VIA + indirizzo, CAP + città |
| **Footer** | Per il Direttore, Dott.ssa Cristina Kullmann, legenda campioni (SI/S/P/U), sede Castenedolo, B.C.S. Priamo, SYNLAB Italia S.r.l., REA/Cap. Soc., SYNLAB Holdco |

**Keep patterns LAB**: nomi analiti (Leucociti, Eritrociti, Emoglobina, Piastrine, Glucosio, Colesterolo, Creatinina), unità di misura (10^9/L, g/L, mg/dL), `*` (fuori range)

#### Laboratorio Bianalisi (lab_bianalisi)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | Bianalisi, Laboratorio di Patologia Clinica, Sede Operativa, Carate (MB) |
| **Dati referto** | Referto Accettazione, codici R/A, ESTERNO SSN, data+sesso |
| **Footer** | Email info@bianalisi, www.bianalisi.it |

#### Anatomia Patologica (anatpat)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | PRESIDIO OSPEDALIERO, LABORATORIO CHE OPERA, UNI EN ISO, Località Montecroce, Desenzano del Garda, U.O.C. ANATOMIA PATOLOGICA |
| **Admin** | Esame ISTOLOGICO I-NNN/NN, Data di accettazione, Tipo amministrativo, Reparto OSP., Medico Dr, Rif, Indirizzo, Comune |
| **Footer** | Data e ora firma |

**Keep patterns**: MATERIALE, NOTIZIE CLINICHE, ESAME MACROSCOPICO, ESAME MICROSCOPICO, DIAGNOSI, grado dell'infiammazione, atrofia, metaplasia, Helicobacter, Marsh

#### Pneumologia/Spirometria (spec_pneumologia)

| Categoria | Contenuto rimosso |
|-----------|------------------|
| **Header** | SC DI PNEUMOLOGIA, SS DI FISIOPATOLOGIA, Data Visita, Stampato il |
| **Tabella paziente** | Nome ID1 Genere, Raggruppamento D.D.N. BMI, Operatore Medico |

**Keep**: Interpretazione, FVC, FEV1, DLCO, deficit

#### Fondazione Richiedei (spec_richiedei)

| Rimosso | Pattern |
|---------|---------|
| Header | Fondazione Richiedei, Via Paolo Richiedei, Gussago, www/PEC, Cod. Fisc., P.IVA |
| Dati | ID. Paziente, Data Esame, Numero Pratica, Numero di telefono |

#### Centro Radiologico Convenzionato (spec_centro_radiologico)

| Rimosso | Pattern |
|---------|---------|
| Header | Numero Referto, Codice Paziente, VIA..., CAP - CITTÀ |
| Admin | Nato il, Tess. Sanitaria, Data Esame Protocollo Esecutore, TSRM, Prestazione MDC |

---

## Sezioni strutturali per tipo documento

### Verbale di Pronto Soccorso

Newline prima di: ANAMNESI ED ESAME OBIETTIVO, ELENCO PRESTAZIONI COMPLETO, CONSULENZE,
DIAGNOSI, ESITO, PROGNOSI, TERAPIA, REFERTI ESAMI

Struttura tipica:
```
Causa dichiarata all'accettazione: [motivo]
Problema principale: [tipo]
Circostanza del trauma: [circostanza]

ANAMNESI ED ESAME OBIETTIVO
AN [anamnesi]
EO [esame obiettivo]

ELENCO PRESTAZIONI COMPLETO
[lista prestazioni]

CONSULENZE
[consulenze specialistiche]

DIAGNOSI
[diagnosi di dimissione PS]

ESITO
[esito: dimesso/ricoverato/trasferito]

TERAPIA
[terapia prescritta]
```

### Lettera di Dimissione

Newline prima di: Diagnosi alla dimissione, Motivo del ricovero, Anamnesi patologica prossima,
Anamnesi ed altri dati, Patologica Remota, Allergie, Scale di valutazione, Interventi chirurgici,
Decorso clinico, Decorso post operatorio, Terapia farmacologica, Terapia alla dimissione,
Procedure ed esami, Condizioni alla dimissione, Indicazioni al follow-up, Altre prescrizioni

Struttura tipica (ASST):
```
Si dimette in data odierna [Sig./Sig.ra NOME], ricoverato dal GG/MM al GG/MM.

Diagnosi alla dimissione
[diagnosi]

Motivo del ricovero
[motivo]

Anamnesi patologica prossima
[storia clinica recente]

Allergie
[allergie note o "Nessuna allergia riferita"]

Interventi chirurgici
[data + descrizione intervento]

Decorso clinico
[decorso]

Terapia farmacologica
[farmaco - via - da: data - a: data]

Terapia alla dimissione
[lista farmaci con posologia]

Indicazioni al follow-up
[controlli e prescrizioni]
```

### Referto Specialistico

Newline prima di: Egregio Collega, Diagnosi:, ANAMNESI PATOLOGICA, Referto,
CONCLUSIONI, COMMENTO, Terapia in atto, Visita, VALUTAZIONE

Struttura tipica:
```
Egregio Collega,
ho valutato in data odierna [paziente].

Diagnosi: [diagnosi]

[corpo del referto specifico per specialità]

Terapia in atto
[tabella farmaci con posologia]

CONCLUSIONI
[conclusioni]

Il Medico Specialista
[nome medico]
```

### Referto Radiologico

Struttura tipica (testo continuo):
```
[Tecnica: tipo esame, mezzo di contrasto]

[Reperti per distretto anatomico]

[Conclusioni diagnostiche]

Medico Radiologo: [nome]
```

### Ecocolordoppler TSA

```
ECOCOLORDOPPLER TRONCHI SOVRAORTICI

DISTRETTO CAROTIDEO DESTRO
[reperti]

DISTRETTO CAROTIDEO SINISTRO
[reperti]

ARTERIE VERTEBRALI
[reperti]

ARTERIE SUCCLAVIE
[reperti]

CONCLUSIONI
[conclusioni]

FOLLOW UP
[indicazioni]

Referto firmato digitalmente da: [nome medico]
```

### Anatomia Patologica

```
MATERIALE
[elenco campioni numerati]

NOTIZIE CLINICHE
[quesito clinico]

ESAME MACROSCOPICO
[descrizione macroscopica per campione]

ESAME MICROSCOPICO
[descrizione microscopica con grading]

DIAGNOSI
[diagnosi istologica]
```

### Referto di Laboratorio

```
Esame  Risultato  U.M.  Valori di riferimento

[SEZIONE] (es. ESAME EMOCROMOCITOMETRICO, Chimica Clinica)
[Analita]  [valore]  [unità]  [range]  [* se fuori range]
[Analita]  [valore]  [unità]  [range]
...

FORMULA LEUCOCITARIA
[analiti formula]
```

---

## Pipeline OCR per PDF grafici

Quando il PDF non contiene testo selezionabile:

1. **Rendering**: PyMuPDF converte ogni pagina in immagine a 3× scala (~216 DPI)
2. **OCR**: Tesseract con lingue `ita+eng`, PSM 3 (automatico), output TSV per coordinate
3. **Parsing**: Le coordinate TSV vengono riscalate alla dimensione pagina originale
4. **Fallback**: Se Tesseract non è disponibile, usa l'estrazione testo nativa di PyMuPDF

Se la qualità OCR è insufficiente, lo script genera un warning e suggerisce di caricare
un'immagine migliore.

---

## Formato output

### Modalità LOCAL_ONLY (senza AI)

Output: testo clinico anonimizzato, con struttura preservata dai newline_before_patterns.
Nessuna analisi dei reperti.

### Modalità AI_ASSISTED (con LLM)

L'output finale segue SEMPRE questa struttura:

```
REPERTI PATOLOGICI SIGNIFICATIVI
[lista reperti con severità, ordinati: (+++) prima, poi (++), poi (+)]
[Se nessun reperto patologico: "Nessun reperto patologico significativo rilevato."]

________________________________________________________________________________

TESTO COMPLETO DEL REFERTO
[corpo del referto estratto, testo pulito]

________________________________________________________________________________

Data referto: [GG/MM/AAAA]
Medico: [Titolo Nome Cognome]
```

### Classificazione severità reperti

- **(+++)** Urgente/critico — Richiede attenzione immediata
  - Neoplasia sospetta, embolia, stenosi critica, frattura instabile, pneumotorace
- **(++)** Da monitorare — Anomalia che richiede follow-up
  - Nodulo < 1 cm, lieve ipertrofia, diverticolosi, ernia discale senza deficit
- **(+)** Incidentale/minore — Scarsa rilevanza clinica immediata
  - Cisti semplici, calcificazioni minime, lipoma, osteofitosi lieve

Formato reperto: `(severità) Reperto sintetico - "Citazione dal testo originale"`

### Regole di formattazione

- Testo continuo: unire righe spezzate dal layout PDF
- A capo solo dopo punteggiatura di chiusura o tra sezioni logiche
- Preservare struttura logica originale (sezioni, paragrafi, sottotitoli)
- Nessun markdown nel corpo del referto: solo testo pulito
- Preservare valori numerici esattamente come riportati
- Elenchi farmacologici: un farmaco per riga con posologia

### Lettera di dimissione

Preservare le sezioni nell'ordine originale: diagnosi alla dimissione, motivo del ricovero,
anamnesi rilevante, decorso clinico, esami diagnostici, procedure/interventi, consulenze,
condizioni alla dimissione, terapia alla dimissione (un farmaco per riga), indicazioni al follow-up.

### Reperti patologici nelle lettere di dimissione

Includere: diagnosi alla dimissione, risultati anomali esami, complicanze, condizioni con follow-up attivo.
NON includere: condizioni croniche note dall'anamnesi (salvo se oggetto specifico del ricovero).

---

## Principi architetturali

### Priorità keep > exclude

I pattern di **keep** hanno SEMPRE priorità superiore ai pattern di **exclude**.
Esempio: una riga che contiene "Diagnosi:" (keep) e "Ospedale" (exclude)
viene MANTENUTA.

### Esclusione dinamica del nome paziente

L'esclusione del nome paziente opera su **due livelli**:

1. **Livello riga** (pre-join): righe che contengono il nome completo vengono escluse
   (a meno che un keep pattern non le preservi):
   - `\bCOGNOME\s+NOME\b`
   - `\bNOME\s+COGNOME\b`

2. **Livello inline** (post-join): il nome viene rimosso anche **dentro** righe preservate
   dai keep pattern (es. "Si dimette la Sig.ra COGNOME NOME, ricoverata..."):
   - Rimuove `Sig./Sig.ra COGNOME NOME,` e varianti
   - Rimuove occorrenze standalone del nome completo in entrambi gli ordini

### Profili dal più specifico al più generico

L'ordine di matching è cruciale: profili con più identifier_patterns vengono testati prima.
Questo evita che `spec_asst` (1 pattern) catturi documenti che dovrebbero matchare
`spec_asst_radiologia` (2 pattern).

### Soglia lunghezza riga per exclude

I blocchi di testo > 80 caratteri che contengono casualmente una keyword amministrativa
NON dovrebbero essere filtrati. È probabile che siano testo clinico. Valutare
sempre il contesto della riga, non solo la keyword.

### Gestione layout multi-colonna

Le lettere di dimissione ASST hanno un sidebar sinistro con dati del personale
(primario, coordinatore, telefoni, email). L'estrazione PDF li mescola con il testo
principale. La gestione opera su **tre livelli**:

1. **Sidebar prefix stripping** (pre-filtro): prima del check keep/exclude, prefissi sidebar
   vengono rimossi dall'inizio delle righe per recuperare il testo clinico che segue.
   Definiti nel campo `sidebar_strip_patterns` del profilo:
   - `"Segreteria "` → recupera date ricovero
   - `"Degenze "` → recupera "Diagnosi alla dimissione"
   - `"traumatologia. "` → recupera "Di seguito si riporta..."
   - `"spedalicivili.it "` → recupera sezione clinica
   - Email troncate (`[a-z]+\d*\.\w+@ `)
   - Numeri telefono (`030/3995610 / 8" `)

2. **Exclude patterns** (livello riga): righe interamente sidebar vengono escluse:
   - Parole singole uppercase (`^[A-Z]{4,}$`) = cognomi personale
   - Email troncate (`[a-z]+@$`) = indirizzi spezzati dal layout
   - Domini isolati (`spedalicivili.it$`)

3. **Inline name removal** (post-join): nomi paziente dentro righe keep vengono rimossi

---

## Apprendimento dalle correzioni

Quando l'utente corregge l'output:

1. **Identificare il pattern** che avrebbe dovuto escludere/includere il testo
2. **Formulare una regola generalizzabile** (non specifica al singolo documento)
3. **Proporre l'aggiornamento** all'utente per conferma
4. **Aggiornare questo SKILL.md** nella sezione appropriata

### Regole apprese dalle correzioni

<!-- Sezione auto-aggiornata. Formato: - [AAAA-MM-GG] AZIONE: regola — Origine: descrizione -->

- [2026-02-25] INIT: 15 profili creati da analisi di 53 documenti reali FSE (ASST Brescia, Maugeri, SYNLAB, Bianalisi, Richiedei). 50/50 successo, 50/50 estrazione nomi.
- [2026-02-25] FIX: Aggiunto sidebar_strip_patterns a dimosp_asst per gestire prefissi sidebar mergiati con testo clinico (Segreteria, Degenze, email troncate, telefoni, domini). Aggiunto inline name removal post-join per rimuovere nomi paziente persistenti in righe keep. Fix pattern Dr. 3+ parole nome.

---

## Gestione PDF multi-pagina

- Processare tutte le pagine in sequenza
- Applicare analisi layout a OGNI pagina (header/footer possono variare o ripetersi)
- Ricostruire testo continuo eliminando interruzioni di pagina artificiali
- Le sezioni delle lettere di dimissione possono attraversare i confini di pagina
- Header/footer ripetuti su ogni pagina vengono automaticamente eliminati dai pattern

---

## Troubleshooting

| Problema | Causa probabile | Soluzione |
|----------|----------------|-----------|
| Testo clinico mancante | Keep pattern mancante per quel tipo di contenuto | Aggiungere regex ai keep_patterns del profilo |
| Testo non-clinico residuo | Pattern exclude non copre quel formato | Aggiungere regex agli exclude_patterns |
| Profilo errato assegnato | Identifier pattern troppo generico | Rendere più specifici i pattern o riordinare priorità |
| Nome paziente non estratto | Formato nome non previsto | Aggiungere patient_name_pattern al profilo |
| Sidebar residuo in dimissioni | Layout multi-colonna con merge inatteso | Aggiungere regex a sidebar_strip_patterns del profilo |
| Nome paziente in righe keep | Keep pattern preserva riga con nome | Gestito automaticamente da inline name removal |
| OCR di scarsa qualità | Bassa risoluzione, foto mossa | Richiedere PDF originale o scansione a >= 300 DPI |
| Dati lab senza struttura | Tabelle non rilevate da pdfplumber | Usare extract_simple come fallback |
