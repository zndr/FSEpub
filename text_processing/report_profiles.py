"""Report parsing profiles for Italian medical documents.

Ported from MedicalReportMonitor C++ (ReportProfile.cpp) and extended
with profiles derived from real-world FSE documents (ASST Spedali Civili,
Maugeri, SYNLAB, Bianalisi, Fondazione Richiedei, etc.).

Each profile defines regex patterns to identify a report type, extract
the patient name, and filter lines (exclude PII, keep medical content).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ReportProfile:
    """Defines parsing rules for a specific type of medical report."""

    name: str = ""
    # Patterns that must ALL match (case-insensitive substring) to identify this profile.
    identifier_patterns: list[str] = field(default_factory=list)
    # Regex patterns with a capture group to extract patient name.
    patient_name_patterns: list[str] = field(default_factory=list)
    # Regex patterns: matching lines are EXCLUDED (PII, headers, footers).
    exclude_patterns: list[str] = field(default_factory=list)
    # Regex patterns: matching lines are ALWAYS KEPT (higher priority than exclude).
    keep_patterns: list[str] = field(default_factory=list)
    # Regex patterns: a newline is inserted before matching text.
    newline_before_patterns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Common PII patterns shared across profiles
# ---------------------------------------------------------------------------

# Core PII patterns that apply to virtually all documents
_COMMON_PII = [
    r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]",          # Codice fiscale
    r"Codice\s+[Ff]iscale",
    r"Tessera\s+Sanitari[ao]",
    r"Cod\.\s+Sanit\.",
]

# Legal footer patterns common to all documents
_COMMON_FOOTER = [
    r"Documento\s+informatico\s+firmato",
    r"Documento\s+elettronico\s+firmato",
    r"stampa\s+costituisce\s+copia",
    r"Archiviato\s+da\s+questo\s+ente",
    r"D\.Lgs\.?\s+n?\.?\s*82/2005",
    r"D\.Lgs\.?\s+82/2005",
    r"disposizioni\s+attuative",
    r"normativa\s+vigente",
    r"D\.P\.R\.?\s+n?\.?\s*445",
    r"D\.P\.R\.?\s+n?\.?\s*513",
    r"D\.C\.P\.M\.",
    r"D\.L\.G\.\s+del",
    r"D\.E\.\s+97/43",
    r"D\.\s*Min\.\s+14",
    r"EURATOM",
    r"IMPORTANTE:\s+il\s+presente\s+referto",
    r"IMPORTANTE:\s+Il\s+presente\s+referto",
    r"conservato\s+dall.assistito",
    r"ripresentato\s+in\s+caso",
    r"firmato\s+digitalmente.*disponibile\s+anche",
    r"www\.crs\.lombardia\.it",
    r"Fascicolo\s+Sanitario\s+Elettronico",
    r"Servizi\s+[Oo]n\s*[Ll]ine\s+per\s+il\s+cittadino",
    r"ID\s+DOCUMENTO[:\s]*\d+",
    r"VERSIONE\s+N",
    r"Stampato\s+il\s*:",
    r"Referto\s+[Ff]irmato\s+digitalmente\s+(?:ai\s+sensi|secondo)",
    r"Referto\s+sottoscritto\s+con\s+firma",
    r"consultare\s+il\s+proprio\s+medico\s+di\s+fiducia",
    r"interpretare\s+i\s+risultati",
    r"Si\s+suggerisce\s+di\s+consultare",
]


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

def _create_ps_asst() -> ReportProfile:
    """Verbale di Pronto Soccorso - ASST Spedali Civili di Brescia.

    Covers both PO Gardone Val Trompia and PO Spedali Civili Brescia.
    """
    return ReportProfile(
        name="ps_asst",
        identifier_patterns=[
            "VERBALE DI PRONTO SOCCORSO",
        ],
        patient_name_patterns=[
            # Format: COGNOME*NOME SECONDO NOME  M/F  CODICEFISCALE
            r"([A-Z]+)\*([A-Z][A-Z\s]+?)\s+[MF]\s+[A-Z]{6}\d{2}",
        ],
        exclude_patterns=[
            # Header istituzionale
            r"DIPARTIMENTO\s+EMERGENZA",
            r"ASST\s+DEGLI\s+SPEDALI\s+CIVILI",
            r"^PO\s+SPEDALI\s+CIVILI",
            r"S\.C\s+PRONTO\s+SOCCORSO",
            r"DEA\s+di\s+II\s+Livello",
            r"PRESIDIO\s+OSPEDALIERO",
            r"RESPONSABILE\s+DR\.",
            r"DIRETTORE:\s+DR\.",
            r"VERBALE\s+DI\s+PRONTO\s+SOCCORSO",
            r"Pagina\s+\d+\s+di\s+\d+",
            r"Cartella\s+Clinica\s+di\s+P\.S\.",
            # Dati anagrafici paziente
            r"Cognome\*nome",
            r"[A-Z]+\*[A-Z]+\s+[MF]\s+[A-Z]{6}",
            r"Nato\s+a:",
            r"Residente\s+a:",
            r"Domiciliato\s+a:",
            r"Regione:\s+LOMBARDIA",
            r"ASL:\s+\d+",
            r"ATS\s+DI\s+BRESCIA",
            r"Telefono\s+rif:",
            r"Cod\.\s+Fiscale:",
            r"Cod\.\s+Sanit\.",
            r"Cittadinanza:",
            # Metadati amministrativi
            r"Modalit.\s+d.invio:",
            r"Codice\s+d.urgenza\s+al\s+triage:",
            r"Data/Ora\s+di\s+Triage:",
            r"Data/Ora\s+di\s+chiusura:",
            r"Data/Ora\s+presa\s+in\s+carico",
            # Footer
            r"Verbale\s+N.\s+\d+",
            r"Versione\s+N.\s+\d+",
            r"Il\s+Medico$",
            r"^\s*[A-Z]+\s+[A-Z]+\s+[A-Z]\d{4,5}\s*$",  # Medico badge code
            r"[A-Z]+,\s+l.\s+\d{2}/\d{2}/\d{4}",  # Luogo e data
            r"GARDONE\s+VAL\s+TROMPIA,\s+l",
            r"BRESCIA,\s+l.\s+\d{2}",
            r"non\s+vale\s+come\s+documentazione\s+ai\s+fini\s+prescrittivi",
            r"RICETTA\s+IN\s+COPIA",
            r"UNICA\s+NON\s+RIPETIBILE",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"ANAMNESI\s+ED\s+ESAME\s+OBIETTIVO",
            r"ELENCO\s+PRESTAZIONI",
            r"CONSULENZE",
            r"DIAGNOSI",
            r"TERAPIA",
            r"ESITO",
            r"PROGNOSI",
            r"^AN\s+",
            r"^EO\s+",
            r"Dolore",
            r"Trauma",
            r"Frattura",
            r"Stecca\s+gessata",
            r"FANS",
            r"Crioterapia",
        ],
        newline_before_patterns=[
            r"ANAMNESI\s+ED\s+ESAME\s+OBIETTIVO",
            r"ELENCO\s+PRESTAZIONI\s+COMPLETO",
            r"CONSULENZE",
            r"DIAGNOSI",
            r"ESITO",
            r"PROGNOSI",
            r"TERAPIA",
            r"REFERTI\s+ESAMI",
        ],
    )


def _create_dimosp_asst() -> ReportProfile:
    """Lettera di Dimissione - ASST Spedali Civili di Brescia.

    Covers PO Gardone V.T. and PO Spedali Civili Brescia.
    """
    return ReportProfile(
        name="dimosp_asst",
        identifier_patterns=[
            "Lettera di dimissione",
            "ASST",
        ],
        patient_name_patterns=[
            # "Sig.ra SENECI REBECCA" / "Sig. KONE ISMAEL"
            r"Sig\.(?:ra)?\s+([A-Z][A-Z\s]+?),?\s+ricoverat[ao]",
            # "Paziente: REBECCA SENECI"
            r"Paziente:\s+([A-Z][A-Z\s]+?)\s+Nosologico",
        ],
        exclude_patterns=[
            # Header istituzionale
            r"ASST\s+DEGLI\s+SPEDALI\s+CIVILI",
            r"PRESIDIO\s+OSPEDALIERO",
            r"Piazzale\s+Spedali\s+Civili",
            r"Via\s+Giovanni\s+XXIII",
            r"25063\s+Gardone",
            r"25125\s+Brescia",
            r"Lettera\s+di\s+dimissione$",
            r"REPARTO[:\s]",
            # Personale ospedaliero
            r"^Primario$",
            r"^/Responsabile$",
            r"^Coordinatore\b",
            r"^Coordinatore\s+inf$",
            r"^Recapiti\s+utili$",
            r"^Recapiti$",
            r"^Segreteria$",
            r"^Degenz[ae]$",
            r"^Indirizzo\s+email$",
            r"^\d{3}[/-]\d{7}$",  # Numeri telefonici
            r"Tel:\s*\d{3}",
            r"030[/-]\d{7}",
            r"@asst-spedalicivili\.it",
            r"@asst-garda\.it",
            # Sidebar fragments from multi-column PDF layout
            r"^ortopedia\.$",
            r"^traumatologia\.$",
            r"^gardone@$",
            r"^chirurgia\d?@",
            r"^psichiatria\d+\.",
            r"^PROF\.\s*[A-Z]+$",
            r"^NAZARIO$",
            # Nomi direttori/primari (riga isolata)
            r"^DR\.\s*[A-Z]+$",
            r"^DR\.[A-Z]+\s+",
            r"^PROF\.\s+[A-Z]+",
            r"^/Responsabile$",
            r"^Direttore$",
            r"^[A-Z]+\s+[A-Z]+$",  # Nomi isolati su riga singola (coordinatore, primario)
            r"^[A-Z]{4,}$",        # Single uppercase word (staff surname from sidebar)
            r"[a-z]+@$",           # Truncated email addresses
            r"spedalicivili\.it$",
            r"^\d{3}/\d{7}\s*/\s*\d",  # Phone numbers like 030/3995610 / 8
            # Dati paziente
            r"Nosologico:\s*\d+",
            r"Nato\s+il\s+\d{2}/\d{2}/\d{4}",
            r"Residente\s+a\s+",
            r"Telefono:\s*\d",
            r"Regime\s+di\s+ricovero:",
            r"Paziente\s+interno",
            r"Ricovero\s+in\s+Day",
            r"Ricovero\s+ordinario",
            # Sidebar email/contact fragments from PDF layout
            r"^[a-z]+\.[a-z]+\.$",  # "ortopedia." "traumatologia."
            r"^[a-z]+@$",           # "gardone@"
            r"^asst-spedalicivili\.it",
            r"Indirizzo\s+email$",
            r"^Prof\.\s+Antonio\s+Vita$",
            r"^Susanna\s+Pedretti$",
            r"^\"Segreteria:",
            r"^\"?Segreteria\s*:?",
            r"^\d{3}/\d{7}(?:\s*/\s*\d+)?$",  # "030/3995233"
            r"^Degenza:",
            r"^Degenze$",
            r"^chirurgia\d+@",
            # Footer pagina
            r"Pagina\s+\d+\s+di\s+\d+",
            r"\d{10}\s+-\s+Versione\s+\d+",
            r"Data\s+Stesura:\s+\d{2}/\d{2}/\d{4}",
            r"Il\s+Medico$",
            r"^Dr\.\s+[A-Z]+\s+[A-Z]+\s+Pagina",
            r"^Dr\.\s+[A-Z]+\s+[A-Z]+$",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Diagnosi\s+alla\s+dimissione",
            r"Motivo\s+del\s+ricovero",
            r"Anamnesi",
            r"Decorso\s+clinico",
            r"Terapia",
            r"Interventi\s+chirurgici",
            r"Procedure",
            r"Condizioni\s+alla\s+dimissione",
            r"Follow.?up",
            r"Indicazioni",
            r"Scale\s+di\s+valutazione",
            r"Allergi[ae]",
            r"Muta$",  # "Patologica Remota: Muta"
            r"Nessun[ao]",
            r"Regolare",
            r"mg",
            r"cp",
            r"Endovenosa",
            r"Si\s+dimette",
            r"Di\s+seguito\s+si\s+riporta",
            r"Sine\s+complicanze",
        ],
        newline_before_patterns=[
            r"Diagnosi\s+alla\s+dimissione",
            r"Motivo\s+del\s+ricovero",
            r"Anamnesi\s+patologica\s+prossima",
            r"Anamnesi\s+ed\s+altri\s+dati",
            r"Patologica\s+Remota",
            r"Allergi[ae]",
            r"Scale\s+di\s+valutazione",
            r"Interventi\s+chirurgici",
            r"Decorso\s+clinico",
            r"Decorso\s+post\s+operatorio",
            r"Terapia\s+farmacologica",
            r"Terapia\s+alla\s+dimissione",
            r"Procedure\s+ed\s+esami",
            r"Condizioni\s+alla\s+dimissione",
            r"Indicazioni\s+al\s+follow",
            r"Altre\s+prescrizioni",
        ],
    )


def _create_dimosp_maugeri() -> ReportProfile:
    """Lettera di Dimissione - Maugeri hospital."""
    return ReportProfile(
        name="dimosp_maugeri",
        identifier_patterns=[
            "Istituto Scientifico di Lumezzane",
            "dimettiamo in data",
        ],
        patient_name_patterns=[
            # "dimettiamo ... la signora LA IACONA ANTONINA (F)"
            r"(?:signora|signor)\s+([A-Z][A-Z\s]+?)\s*\([MF]\)",
            r"Paziente:\s+([A-Z][A-Z\s]+?)(?:\s{2,}|$)",
        ],
        exclude_patterns=[
            # Intestazione Maugeri
            r"Istituti\s+Clinici\s+Scientifici\s+Maugeri",
            r"Via\s+Salvatore\s+Maugeri",
            r"C\.F\.\s+e\s+P\.IVA",
            r"Iscrizione\s+Rea:",
            r"Istituto\s+Scientifico\s+di\s+Lumezzane",
            r"Via\s+Mazzini",
            r"25065\s+Lumezzane",
            r"Dirigente\s+Responsabile",
            r"Tel\.?\s+030",
            r"URP\s+030",
            r"E-mail:",
            r"lumezzane@icsmaugeri",
            r"Lumezzane,\s+l",
            r"Dr\.\s+[A-Za-z]+\s+[A-Za-z]+\s+DI\s+PIETRO",
            # Dati paziente
            r"nato/a\s+il",
            r"degente\s+presso\s+la\s+nostra",
            r"Data\s+di\s+Nascita:",
            r"Codice\s+Paz\.?\s*(?:ID)?:",
            r"PK-\d+",
            r"Indirizzo:",
            r"Citt.:",
            r"Telefono:",
            r"C\.F\.:",
            r"Numero\s+Cartella:",
            r"Tipo:\s+Esterno",
            r"Provenienza:",
            # Footer Maugeri
            r"Pagina\s+\d+\s+di\s+\d+",
            r"ICSM\s+SIO\s+DIMI",
            r"Firmato\s+da\s+",
            r"in\s+data\s+e\s+ora:",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"DIAGNOSI",
            r"Motivo\s+del\s+ricovero",
            r"INQUADRAMENTO\s+CLINICO",
            r"DECORSO",
            r"TERAPIA",
            r"Allergi[ae]",
            r"Comorbidit",
            r"mg",
        ],
        newline_before_patterns=[
            r"DIAGNOSI\s+DI\s+ACCETTAZIONE",
            r"Motivo\s+del\s+ricovero",
            r"Allergi[ae]\s+[Ff]armaci",
            r"Allergi[ae]\s+[Aa]limenti",
            r"Comorbidit",
            r"INQUADRAMENTO\s+CLINICO",
            r"DECORSO\s+CLINICO",
            r"TERAPIA",
            r"ESAMI\s+ESEGUITI",
            r"CONCLUSIONI",
            r"INDICAZIONI\s+ALLA\s+DIMISSIONE",
        ],
    )


def _create_spec_asst() -> ReportProfile:
    """Referto Specialistico - ASST Spedali Civili di Brescia.

    Covers ambulatory reports from PO Spedali Civili, PO Gardone V.T.,
    Poliambulatorio Via Biseo, etc. Handles many specialties:
    oculistica, chirurgia vascolare, diabetologia, endoscopia, ortopedia,
    uroginecologia, accessi vascolari, etc.
    """
    return ReportProfile(
        name="spec_asst",
        identifier_patterns=[
            "ASST DEGLI SPEDALI CIVILI DI BRESCIA",
        ],
        patient_name_patterns=[
            # "Paziente SCARONI CATIA   Codice fiscale"
            r"Paziente\s+([A-Z][A-Z\s]+?)\s+Codice\s+fiscale",
            # "Sig.ra CONTER SARA   Codice Fiscale"
            r"Sig\.(?:ra)?\s+([A-Z][A-Z\s]+?)\s+Codice\s+Fiscale",
            # "Sig.ra PINELLI LUCIA" (radiologia ASST)
            r"Sig\.(?:ra)?\s+([A-Z][A-Z\s]+?)$",
            # "la Sua paziente CONTER SARA (nata il"
            r"[Ss]u[ao]\s+paziente\s+([A-Z][A-Z\s]+?)\s+\(nat[ao]",
            # "il suo assistito CINELLI LUIGIA nato il"
            r"assistit[ao]\s+([A-Z][A-Z\s]+?)\s+nat[ao]\s+il",
        ],
        exclude_patterns=[
            # Header ASST
            r"ASST\s+DEGLI\s+SPEDALI\s+CIVILI",
            r"P\.O\.\s+SPEDALI\s+CIVILI",
            r"P\.O\.\s+GARDONE",
            r"POLIAMBULATORIO\s+DI\s+VIA",
            r"BRESCIA\s+-\s+P\.LE\s+SPEDALI",
            r"BRESCIA\s+-\s+VIA\s+BISEO",
            r"GARDONE\s+VAL\s+TROMPIA\s+-\s+VIA",
            r"UNITA.\s+AZIENDALE",
            # Reparto/Ambulatorio
            r"^S\.C\.\s+",
            r"^Responsabile\s*:",
            r"^Direttore:",
            r"^AMBULATORIO\s+",
            r"Ambulatorio$",
            # Personale medico (solo intestazione)
            r"^Dott\.(?:ssa)?\s+[A-Z][a-z]+\s+[A-Z][a-z]+$",
            r"^Prof\.\s+[A-Z]",
            # Dati paziente
            r"Paziente\s+[A-Z]+\s+[A-Z]+\s+Codice\s+fiscale",
            r"Sig\.(?:ra)?\s+[A-Z]+\s+[A-Z]+\s+Codice",
            r"Nascita\s+\d{2}/\d{2}/\d{4}",
            r"N(?:umero)?\s+[Aa]ccesso",
            r"Residenza\s+",
            r"Tessera\s+Sanitaria",
            r"Prestazioni\s+[A-Z]",
            # Footer ASST
            r"Il\s+Medico\s+Specialista",
            r"\(Timbro\s+e\s+Firma\)",
            r"Validato\s+in\s+Data",
            r"Firmato\s+in\s+Data",
            r"^\d+/\d+$",  # "1/1" or "1/2"
            r"Brescia,\s+\d{2}/\d{2}/\d{4}",
            # Radiologia ASST (Gardone VT, Spedali Civili)
            r"^Dipartimento\s+di\s+Diagnostica",
            r"^U\.O\.\s+Radiologia",
            r"Data\s+Nascita\s*:",
            r"^CF:",
            r"ID\s+Paz\s*:",
            r"ID\s+Anag\.C\s*:",
            r"Id\s+episodio\s*:",
            r"Provenienza\s*:",
            r"Medico\s+prescrittore\s*:",
            r"Quesito\s+diagnostico\s*:",
            r"Coord\.\s+TSRM",
            r"TSRM\s+",
            r"Data\s+esame:",
            r"^\*\d+\*$",  # Barcode
            r"^Dr\.(?:ssa)?\s+[A-Z][a-z]+\s+[A-Z][a-z]+$",
            r"^Tel\.\s+\d{3}",
            r"e-mail:",
            # Medico refertante con CF (footer)
            r"[A-Z]+\s+[A-Z]+\s+\([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\)",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Egregio\s+Collega",
            r"Referto",
            r"Diagnosi:",
            r"CONCLUSIONI",
            r"COMMENTO",
            r"ANAMNESI",
            r"Terapia\s+in\s+atto",
            r"POSOLOGIA",
            r"mg",
            r"gtt",
            r"cp",
        ],
        newline_before_patterns=[
            r"Egregio\s+Collega",
            r"Diagnosi:",
            r"ANAMNESI\s+PATOLOGICA",
            r"Referto\b",
            r"CONCLUSIONI",
            r"COMMENTO",
            r"Terapia\s+in\s+atto",
            r"Visita\b",
            r"VALUTAZIONE",
        ],
    )


def _create_spec_asst_radiologia() -> ReportProfile:
    """Referto Radiologico - ASST Spedali Civili (Radiologia Gardone VT / Brescia).

    More specific than spec_asst for radiology reports which have a
    different header format with staff listing.
    """
    return ReportProfile(
        name="spec_asst_radiologia",
        identifier_patterns=[
            "ASST DEGLI SPEDALI CIVILI DI BRESCIA",
            "Diagnostica per Immagini",
        ],
        patient_name_patterns=[
            r"Sig\.(?:ra)?\s+([A-Z][A-Z\s]+?)$",
            r"Sig\.\s+([A-Z][A-Z\s]+?)$",
        ],
        exclude_patterns=[
            # Header
            r"ASST\s+DEGLI\s+SPEDALI\s+CIVILI",
            r"Dipartimento\s+di\s+Diagnostica",
            r"S\.C\.\s+Radiologia",
            r"U\.O\.\s+Radiologia",
            r"Presidio\s+Ospedaliero",
            r"Responsabile\s*:",
            r"Direttore:",
            # Staff listing
            r"^Dr\.(?:ssa)?\s+[A-Z][a-z]+\s+[A-Z][a-z]+$",
            r"^Coord\.\s+TSRM$",
            r"^Tel\.\s+\d{3}",
            r"e-mail:",
            # Dati paziente
            r"Sig\.(?:ra)?\s+[A-Z]+\s+[A-Z]+$",
            r"Sig\.\s+[A-Z]+\s+[A-Z]+$",
            r"Data\s+Nascita\s*:",
            r"^CF:",
            r"ID\s+Paz\s*:",
            r"ID\s+Anag\.C",
            r"^\*\d+\*",
            r"Id\s+episodio",
            r"Provenienza\s*:",
            r"Medico\s+prescrittore",
            r"Quesito\s+diagnostico",
            r"Data\s+esame:",
            r"Data\s+Esame:",
            r"CUP",
            r"TSRM\s+",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Medico\s+Radiologo:",
            r"Medico\s+refertante:",
            r"CONCLUSIONI",
        ],
        newline_before_patterns=[
            r"CONCLUSIONI",
            r"Medico\s+Radiologo",
        ],
    )


def _create_spec_maugeri_cardio() -> ReportProfile:
    """Referti cardiologici Maugeri (ECO, Ecostress, Holter, ecc.).

    Covers: Ecocolordopplergrafia transtoracica, Ecostress, Holter,
    visite cardiologiche, ecc. from UO Cardiologia Riabilitativa.
    """
    return ReportProfile(
        name="spec_maugeri_cardio",
        identifier_patterns=[
            "Istituto Scientifico di Lumezzane",
            "RIABILITAZIONE",
        ],
        patient_name_patterns=[
            r"Paziente:\s+([A-Z][A-Z\s]+?)(?:\s+Anni:|\s{2,}|$)",
            r"Paziente:\s+([A-Z][A-Z\s]+?)$",
        ],
        exclude_patterns=[
            # Intestazione Maugeri
            r"Istituti\s+Clinici\s+Scientifici\s+Maugeri",
            r"Via\s+Salvatore\s+Maugeri",
            r"C\.F\.\s+e\s+P\.IVA",
            r"Iscrizione\s+Rea:",
            r"Istituto\s+Scientifico\s+di\s+Lumezzane",
            r"UO\s+RIABILITAZIONE",
            r"Dirigente\s+Responsabile",
            r"Via\s+Mazzini",
            r"25065\s+Lumezzane",
            r"Tel\.?\s+030",
            r"URP\s+030",
            r"Fax\.?\s+030",
            r"E-mail:",
            r"lumezzane@icsmaugeri",
            r"Ambulatorio[,:]\s+LU",
            r"Ambulatorio:\s+LU",
            r"Dr\.\s+[A-Za-z]+\s+[A-Z]+\s*\.$",
            # Data e luogo
            r"Lumezzane,\s+l?",
            r"Lumezzane,\s+\d",
            # Dati paziente
            r"Paziente:",
            r"Data\s+di\s+[Nn]ascita:",
            r"Anni:\s+\d+",
            r"Sesso:\s+(?:Maschio|Femmina)",
            r"Codice\s+Paz\.?\s*(?:ID)?:",
            r"PK-\d+",
            r"Indirizzo:",
            r"Citt.:",
            r"Telefono:",
            r"C\.F\.:",
            r"Provenienza:",
            r"Esterno$",
            r"Descrizione\s+Esame:",
            r"Quesito\s+Diagnostico:",
            r"Numero\s+Cartella:",
            r"Tipo:\s+Esterno",
            r"^Operatore:",
            r"^_+$",  # Linee di separazione
            r"Residenza:",
            r"Prestazioni\s+richieste",
            r"Codice\s+Prestazione\s+Stato",
            r"A4H@",
            r"EROGATA$",
            r"Numero\s+Prenotazione:",
            # Footer Maugeri
            r"il:\s+\d{2}/\d{2}/\d{4}",
            r"Ora:\s+\d{2}:\d{2}",
            r"Note\s+di\s+reperibilit",
            r"Le\s+informazioni\s+sanitarie",
            r"medico\s+curante",
            r"DPR\s+445/2000",
            r"Tutti\s+gli\s+esami\s+sono\s+archiviati",
            r"mancata\s+consegna",
            r"richiederne\s+copia",
            r"Istituti\s+Clinici\s+Scientifici\s+Spa",
            r"Pagina\s+\d+\s+di\s+\d+",
            r"Sistema\s+Sanitario",
            r"Regione\s+Lombardia",
            r"che,\s+nel\s+caso\s+di\s+dubbi",
            r"necessit.\s+di\s+approfondimenti",
            r"pu.\s+rivolgersi\s+allo\s+specialista",
            r"che\s+ha\s+redatto\s+il\s+referto",
            r"il\s+\d{2}/\d{2}/\d{2}\s+alle\s+\d{2}:\d{2}",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Referto\s+firmato\s+digitalmente\s+da:",
            r"CONCLUSIONI",
            r"FOLLOW\s+UP",
            r"DISTRETTO\s+CAROTIDEO",
            r"ARTERIE\s+VERTEBRALI",
            r"ARTERIE\s+SUCCLAVIE",
            r"mg",
        ],
        newline_before_patterns=[
            r"DISTRETTO\s+CAROTIDEO\s+SIN",
            r"ARTERIE\s+VERTEBRALI",
            r"ARTERIE\s+SUCCLAVIE",
            r"CONCLUSIONI",
            r"FOLLOW\s+UP",
            r"Referto\s+firmato\s+digitalmente\s+da:",
            r"Dati\s+Generali",
        ],
    )


def _create_spec_pneumologia() -> ReportProfile:
    """Spirometria / Prove funzionali respiratorie - ASST.

    Reports from Pneumologia with tabular spirometry data.
    """
    return ReportProfile(
        name="spec_pneumologia",
        identifier_patterns=[
            "PNEUMOLOGIA",
            "CAPACIT",
        ],
        patient_name_patterns=[
            r"^([A-Z]+\s+[A-Z]+)\s+--\s+(?:Maschio|Femmina)",
        ],
        exclude_patterns=[
            r"^SC\s+DI\s+PNEUMOLOGIA",
            r"SS\s+DI\s+FISIOPATOLOGIA",
            r"Direttore:",
            r"Responsabile:",
            r"Stampato\s+il",
            r"Data\s+Visita",
            r"^Nome\s+ID1",
            r"^Raggruppamento\s+",
            r"^ASST\s+",
            r"D\.D\.N\.",
            r"BMI\s+\(kg/m2\)",
            r"Fumatore\s+",
            r"^Operatore\s+Medico",
            r"[A-Z]\.\s+BANDERA",
            r"DR\s+G\s*\.",
            r"Classe\s+2",
            r"Etnia$",
            r"^16169$",
            r"^Firma:$",
            r"^--$",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Interpretazione",
            r"FVC",
            r"FEV1",
            r"DLCO",
            r"deficit",
        ],
        newline_before_patterns=[
            r"Interpretazione",
            r"CAPACIT.\s+VITALE",
            r"DIFFUSIONE",
        ],
    )


def _create_spec_richiedei() -> ReportProfile:
    """Referti radiologici Fondazione Richiedei (Gussago)."""
    return ReportProfile(
        name="spec_richiedei",
        identifier_patterns=[
            "Richiedei",
        ],
        patient_name_patterns=[
            r"ID\.\s+Paziente:\s+\d+\s+([A-Za-z]+\s+[A-Za-z]+)",
        ],
        exclude_patterns=[
            r"Fondazione.*Richiedei",
            r"Via\s+Paolo\s+Richiedei",
            r"Gussago",
            r"www\.richiedei\.it",
            r"info@richied",
            r"PEC:",
            r"Cod\.\s+Fisc\.:",
            r"P\.IVA:",
            r"Servizio\s+di\s+Radiologia",
            r"ID\.\s+Paziente:",
            r"Data\s+Esame:",
            r"Numero\s+di\s+telefono:",
            r"Data\s+di\s+Nascita:",
            r"Provenienza:",
            r"Numero\s+Pratica:",
            r"VIA\s+[A-Z]+\s+\d+",
            r"25065\s+LUMEZZANE",
            r"\d{10}\s+\d{10}",  # Double phone numbers
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Esame\s+eseguito",
            r"CONCLUSIONI",
        ],
    )


def _create_spec_strapparava() -> ReportProfile:
    """Referti radiologici con formato header compatto (es. centri convenzionati).

    Format: patient name and address on top, then exam data table.
    Identified by "Numero Referto:" in header.
    """
    return ReportProfile(
        name="spec_centro_radiologico",
        identifier_patterns=[
            "Numero Referto:",
            "Codice Paziente:",
        ],
        patient_name_patterns=[
            r"^([A-Z]+\s+[A-Z]+)\s+Numero\s+Referto",
        ],
        exclude_patterns=[
            r"Numero\s+Referto:",
            r"Codice\s+Paziente:",
            r"^VIA\s+",
            r"^\d{5}\s+-\s+[A-Z]+",
            r"Nato\s+il:",
            r"Tess\.\s+Sanitaria:",
            r"Data\s+Esame\s+Protocollo\s+Esecutore",
            r"TSRM\s+",
            r"Prestazione\s+MDC\s+Q\.t",
            r"^Esame\s+eseguito$",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"L.indagine",
            r"Gonartrosi",
            r"CONCLUSIONI",
        ],
    )


def _create_anatpat() -> ReportProfile:
    """Referto Anatomia Patologica - ASST del Garda, ecc."""
    return ReportProfile(
        name="anatpat",
        identifier_patterns=[
            "ANATOMIA PATOLOGICA",
        ],
        patient_name_patterns=[
            # Standalone all-caps name line after "Tipo amministrativo"
            r"Tipo\s+amministrativo.*\n([A-Z]{2,}(?:\s+[A-Z]{2,})+)",
            # "Egr. Sig.\n...\nNAME"
            r"Egr\.\s+Sig\.\s*\n.*\n\s*([A-Z]{2,}(?:\s+[A-Z]{2,})+)",
            r"([A-Z]+\s+[A-Z]+(?:\s+[A-Z]+)?)\s+Nato\s+il",
        ],
        exclude_patterns=[
            # Header
            r"PRESIDIO\s+OSPEDALIERO",
            r"LABORATORIO\s+CHE\s+OPERA",
            r"SISTEMA\s+DI\s+GESTIONE",
            r"UNI\s+EN\s+ISO",
            r"Localit.\s+Montecroce",
            r"Desenzano\s+del\s+Garda",
            r"U\.O\.C\.\s+ANATOMIA",
            r"tel\.:",
            r"e\s+mail:",
            r"Direttore:",
            # Dati admin
            r"Esame\s+ISTOLOGICO\s+I-\d+",
            r"Data\s+di\s+accettazione",
            r"Tipo\s+amministrativo",
            r"Egr\.\s+Sig\.",
            r"Reparto\s+OSP\.",
            r"Medico\s+Dr",
            r"Rif:",
            r"Indirizzo\s+V\.",
            r"Comune\s+[A-Z]+",
            r"Telefono$",
            r"Nato\s+il\s+\d{2}/\d{2}/\d{4}",
            # Footer
            r"Pagina\s+\d+\s+di\s+\d+",
            r"Data\s+e\s+ora\s+firma",
            r"firmato\s+(?:digital|elettronic)mente\s+ai\s+sensi",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"MATERIALE",
            r"NOTIZIE\s+CLINICHE",
            r"ESAME\s+MACROSCOPICO",
            r"ESAME\s+MICROSCOPICO",
            r"DIAGNOSI",
            r"grado\s+dell",
            r"atrofia",
            r"metaplasia",
            r"Helicobacter",
            r"Marsh",
        ],
        newline_before_patterns=[
            r"MATERIALE",
            r"NOTIZIE\s+CLINICHE",
            r"ESAME\s+MACROSCOPICO",
            r"ESAME\s+MICROSCOPICO",
            r"DIAGNOSI",
        ],
    )


def _create_lab_synlab() -> ReportProfile:
    """Referti di laboratorio SYNLAB."""
    return ReportProfile(
        name="lab_synlab",
        identifier_patterns=[
            "synlab",
        ],
        patient_name_patterns=[
            # Standalone name line (all caps, 2-3 words)
            r"^([A-Z]{2,}(?:\s+[A-Z]{2,}){1,2})\s*$",
            # Name before "Nato/a il"
            r"([A-Z]{2,}(?:\s+[A-Z]{2,}){1,2})\s*\nNato/a\s+il",
        ],
        exclude_patterns=[
            # Header SYNLAB
            r"www\.synlab\.it",
            r"customerservice",
            r"SYNLAB",
            r"Indirizzo:",
            r"Provenienza\s*:",
            r"ESTERNO\s+SSN",
            r"Codice\s+Lab\.",
            r"Id\s+Referto:",
            r"Data\s+Referto:",
            r"Richiesta\s*:",
            r"Data\s+Prelievo:",
            r"Versione\s+referto:",
            r"Pagina\s+\d+\s+di\s+\d+",
            r"BP\d+\s+SYNLAB",
            # Dati paziente
            r"Nato/a\s+il",
            r"Sesso:\s+[MF]",
            r"VIA\s+[A-Z]+",
            r"^\d{5}\s+[A-Z]+\s+BS$",
            # Footer
            r"Per\s+il\s+Direttore",
            r"Dott\.ssa\s+Cristina\s+Kullmann",
            r"RISULTATI\s+DEL\s+PRESENTE\s+REFERTO",
            r"EFFICACIA\s+DIAGNOSTICA",
            r"INTERPRETATI\s+DAL\s+PROPRIO\s+MEDICO",
            r"Legenda:\s+SI\s+=\s+sangue",
            r"sede\s+di\s+Castenedolo",
            r"Laboratorio\s+di\s+Patologia",
            r"Via\s+Beato\s+Lodovico",
            r"Castenedolo",
            r"Direttore\s+Laboratorio",
            r"B\.C\.S\.\s+Priamo",
            r"soc\.\s+unipersonal",
            r"Via\s+Martiri\s+delle\s+Foibe",
            r"REA\s+MB",
            r"Cap\.\s+Soc\.",
            r"soggetta\s+a\s+direzione",
            r"SYNLAB\s+Holdco",
            r"Valori\s+di\s+riferimento\s+aggiornati",
            r"Referto\s+firmato\s+digitalmente\s+da",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Leucociti",
            r"Eritrociti",
            r"Emoglobina",
            r"Piastrine",
            r"Glucosio",
            r"Colesterolo",
            r"Creatinina",
            r"10\^9/L",
            r"g/L",
            r"mg/dL",
            r"\*$",
        ],
        newline_before_patterns=[
            r"ESAME\s+EMOCROMOCITOMETRICO",
            r"FORMULA\s+LEUCOCITARIA",
        ],
    )


def _create_lab_bianalisi() -> ReportProfile:
    """Referti di laboratorio Bianalisi."""
    return ReportProfile(
        name="lab_bianalisi",
        identifier_patterns=[
            "Bianalisi",
        ],
        patient_name_patterns=[
            # "Provenienza MULTARI MARIA ROSA"
            r"Provenienza\s+([A-Z]+\s+[A-Z]+(?:\s+[A-Z]+)?)\s*$",
            r"^([A-Z]+\s+[A-Z]+(?:\s+[A-Z]+)?)\s+Codice\s+Fiscale",
        ],
        exclude_patterns=[
            # Header Bianalisi
            r"^Bianalisi$",
            r"Laboratorio\s+di\s+Patologia\s+Clinica",
            r"Microbiologia\s+e\s+Virologia",
            r"Direttore:",
            r"Sede\s+Operativa",
            r"Via\s+Don\s+Costante",
            r"Carate\s+\(MB\)",
            # Dati referto
            r"Referto\s+Accettazione",
            r"^R\d+[A-Z]\d+",
            r"del\s+\d{2}/\d{2}/\d{4}",
            r"^A\d+[A-Z]\d+",
            r"Provenienza$",
            r"ESTERNO\s+SSN",
            r"^[MF]-\d{2}/\d{2}/\d{4}",
            r"anni\s+\d+\)",
            r"VIA\s+[A-Z]+",
            r"^\d{5}\s+[A-Z]+$",
            # Footer
            r"Email:\s+info@bianalisi",
            r"www\.bianalisi\.it",
            r"Referto\s+stampato\s+il",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Glucosio",
            r"Colesterolo",
            r"Trigliceridi",
            r"Creatinina",
            r"eGFR",
            r"AST",
            r"ALT",
            r"mg/dL",
            r"U/L",
            r"mL/min",
        ],
        newline_before_patterns=[
            r"Chimica\s+Clinica",
            r"Ematologia",
        ],
    )


def _create_rx_maugeri() -> ReportProfile:
    """Radiological reports from Maugeri hospital."""
    return ReportProfile(
        name="rx_Maugeri",
        identifier_patterns=[
            "Istituto Scientifico di Lumezzane",
            "Servizio di Diagnostica per Immagini",
        ],
        patient_name_patterns=[
            r"Sig\./Sig\.ra:\s+([A-Za-z][A-Za-z\s]+?)(?:\s{2,}|ID\s+Paziente)",
        ],
        exclude_patterns=[
            r"Istituto\s+Scientifico",
            r"Servizio\s+di\s+Diagnostica",
            r"Primario:",
            r"Tel\.",
            r"Fax\.",
            r"Email:",
            r"Sig\./Sig\.ra:",
            r"Data\s+di\s+Nascita:",
            r"\d{2}/\d{2}/\d{4}",
            r"Codice\s+Fiscale:",
            r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]",
            r"ID\s+Paziente:",
            r"PK-\d+",
            r"N\.\s+di\s+accesso:",
            r"\d{10}",
            r"Provenienza:",
            r"ESTERNO",
            r"Prestazione\s+eseguita:",
            r"Schedulazione:",
            r"Esecuzione:",
            r"Classe\s+dose:",
            r"Data\s+validazione",
            r"Documento\s+informatico",
            r"stampa\s+costituisce",
            r"D\.Lgs",
            r"Pag\s+\d+\s+di\s+\d+",
            r"TSRM:",
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Medico\s+Radiologo:",
        ],
    )


def _create_tsa_maugeri() -> ReportProfile:
    """Ecocolordoppler TSA reports from Maugeri hospital."""
    return ReportProfile(
        name="tsa_maugeri",
        identifier_patterns=[
            "ECOCOLORDOPPLER TRONCHI SOVRAORTICI",
        ],
        patient_name_patterns=[
            r"Paziente:\s+([A-Z]+\s+[A-Z]+(?:\s+[A-Z]+)?)\s+Anni:",
        ],
        exclude_patterns=[
            # Intestazione istituto
            r"Istituti\s+Clinici\s+Scientifici\s+Maugeri",
            r"Via\s+Salvatore\s+Maugeri",
            r"C\.F\.\s+e\s+P\.IVA",
            r"Iscrizione\s+Rea:",
            r"Istituto\s+Scientifico\s+di\s+Lumezzane",
            r"UO\s+RIABILITAZIONE",
            r"Dirigente\s+Responsabile:",
            r"Via\s+Mazzini",
            r"25065\s+Lumezzane",
            r"Tel\s+030",
            r"URP\s+030",
            r"E-mail:",
            r"lumezzane@icsmaugeri",
            r"Ambulatorio,\s+LU",
            # Luogo e data
            r"Lumezzane,\s+\d{1,2}/\d{1,2}/\d{4}",
            # Dati paziente
            r"Paziente:",
            r"Data\s+di\s+Nascita:",
            r"Anni:\s+\d+",
            r"Sesso:\s+Maschio",
            r"Sesso:\s+Femmina",
            r"Codice\s+Paz\.\s+ID:",
            r"PK-\d+",
            r"Indirizzo:",
            r"Citt[aà]:",
            r"Telefono:",
            r"\d{10}",
            r"C\.F\.:",
            r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]",
            r"Provenienza:",
            r"Esterno",
            r"Descrizione\s+Esame:",
            r"Quesito\s+Diagnostico:",
            # Footer e note legali
            r"il:\s+\d{2}/\d{2}/\d{4}",
            r"Ora:\s+\d{2}:\d{2}",
            r"Note\s+di\s+reperibilit",
            r"Le\s+informazioni\s+sanitarie",
            r"medico\s+curante",
            r"Documento\s+elettronico\s+firmato",
            r"DPR\s+445/2000",
            r"D\.Lgs\.\s+82/2005",
            r"Tutti\s+gli\s+esami\s+sono\s+archiviati",
            r"mancata\s+consegna\s+del\s+supporto",
            r"richiederne\s+copia",
            r"Istituti\s+Clinici\s+Scientifici\s+Spa",
            r"Pagina\s+\d+\s+di\s+\d+",
            r"Sistema\s+Sanitario",
            r"Regione\s+Lombardia",
            # Testo dopo firma medico
            r"che,\s+nel\s+caso\s+di\s+dubbi",
            r"necessit.\s+di\s+approfondimenti",
            r"pu.\s+rivolgersi\s+allo\s+specialista",
            r"che\s+ha\s+redatto\s+il\s+referto",
            r"il\s+\d{2}/\d{2}/\d{2}\s+alle\s+\d{2}:\d{2}",
        ],
        keep_patterns=[
            r"Referto\s+firmato\s+digitalmente\s+da:",
            r"CONCLUSIONI",
            r"FOLLOW\s+UP",
        ],
        newline_before_patterns=[
            r"DISTRETTO CAROTIDEO SIN",
            r"ARTERIE VERTEBRALI",
            r"ARTERIE SUCCLAVIE",
            r"CONCLUSIONI",
            r"FOLLOW UP",
            r"Referto firmato",
        ],
    )


def _create_default() -> ReportProfile:
    """Generic default profile for any Italian medical report.

    This profile is comprehensive and handles common patterns across
    all Italian hospital document types.
    """
    return ReportProfile(
        name="default",
        identifier_patterns=[],
        patient_name_patterns=[
            # ASST format: "Paziente NAME Codice fiscale"
            r"Paziente\s+([A-Z][A-Z\s]+?)\s+Codice\s+fiscale",
            # Maugeri format: "Sig./Sig.ra: Name  ID Paziente"
            r"Sig\./Sig\.ra:\s+([A-Za-z][A-Za-z\s]+?)(?:\s{2,}|ID\s+Paziente)",
            r"Sig\.(?:ra)?\s+([A-Z][A-Z\s]+?)(?:\s+Codice|\s{2,}|$)",
            # Maugeri cardio: "Paziente: NAME  Anni:" or end of line
            r"Paziente:\s+([A-Z][A-Z\s]+?)(?:\s+Anni:|\s{2,}|Data|$)",
            # DIMOSP: "Sig. NAME, ricoverato"
            r"Sig\.(?:ra)?\s+([A-Z][A-Z\s]+?),?\s+ricoverat",
            # PS: "COGNOME*NOME SECONDO  M/F  CF"
            r"([A-Z]+)\*([A-Z][A-Z\s]+?)\s+[MF]\s+[A-Z]{6}\d{2}",
        ],
        exclude_patterns=[
            # Intestazioni istituzionali (any ASST)
            r"^ASST\s+",
            r"^Istitut[oi]",
            r"^IRCCS",
            r"^Ospedale",
            r"^PRESIDIO\s+OSPEDALIERO",
            r"^Fondazione",
            r"^Piazzale\s+Spedali",
            r"^P\.O\.\s+",
            r"^POLIAMBULATORIO",
            r"^Localit.\s+Montecroce",
            r"^Servizio\s+di",
            r"^S\.C\.\s+",
            r"^U\.O\.\s+",
            r"^U\.O\.C\.\s+",
            r"^Dipartimento\s+di\s+Diagnostica",
            r"^LABORATORIO\s+CHE\s+OPERA",
            r"^SSVD\s+DI\s+",
            r"^Viale\s+Mazzini",
            r"Sistema\s+di\s+gestione\s+per\s+la\s+Qualit",
            r"ISO\s+9001",
            r"^simt@",
            r"@asst-franciacorta",
            r"Numero\s+Prenotazione:",
            # Personale
            r"^Primario[:\s]",
            r"^Direttore:",
            r"^Responsabile\s*:",
            r"^Dirigente\s+Responsabile",
            r"^Coordinatore\b",
            # Recapiti
            r"^Tel\.?\s+\d",
            r"^Fax\.?\s+\d",
            r"^URP\s+\d",
            r"^Email:",
            r"^E-mail:",
            r"^e-mail:",
            r"@icsmaugeri",
            r"@asst-spedalicivili",
            r"@asst-garda",
            r"www\.\w+\.it",
            r"^Recapiti\s+utili",
            r"^Recapiti$",
            r"^Segreteria$",
            r"^Degenz[ae]$",
            r"^Indirizzo\s+email$",
            # Dati anagrafici paziente
            r"Sig\./Sig\.ra:\s+[A-Z]",
            r"Sig\.(?:ra)?\s+[A-Z]+\s+[A-Z]+\s+Codice",
            r"Paziente\s+[A-Z]+\s+[A-Z]+\s+(?:Codice|Nosologico)",
            r"Paziente:\s+[A-Z]+\s+[A-Z]+\s+(?:Anni|Nosologico)",
            r"Cognome\*nome",
            r"[A-Z]+\*[A-Z]+\s+[MF]\s+[A-Z]{6}",
            r"Data\s+di\s+[Nn]ascita",
            r"Data\s+[Nn]ascita\s*:",
            r"Nato\s+(?:a|il)\s*:",
            r"Nato/a\s+il",
            r"Nascita\s+\d{2}/\d{2}/\d{4}",
            r"Residenz[ea]\s+",
            r"Domiciliato\s+a:",
            r"Cittadinanza:",
            r"Telefono\s*(?:rif)?:",
            r"N(?:umero)?\s+[Aa]ccesso",
            r"Nosologico:\s*\d+",
            r"ID\s+Paz(?:iente)?:",
            r"Codice\s+Paz\.?\s*(?:ID)?:",
            r"PK-\d+",
            r"Cod\.\s+Sanit\.",
            r"Regime\s+di\s+ricovero:",
            # Metadati amministrativi
            r"Provenienza",
            r"Prestazione\s+eseguita",
            r"Prestazioni\s+[A-Z]",
            r"Schedulazione:",
            r"Esecuzione:",
            r"Classe\s+dose:",
            r"Data\s+validazione",
            r"Data\s+esame:",
            r"Medico\s+prescrittore",
            r"^CUP",
            r"Id\s+episodio",
            r"ID\s+Anag",
            r"^\*\d+\*",
            r"Quesito\s+diagnostico",
            r"Numero\s+Cartella:",
            r"Modalit.\s+d.invio:",
            r"ASL:\s+\d+",
            r"ATS\s+DI\s+BRESCIA",
            r"Regione:\s+LOMBARDIA",
            # PS admin metadata
            r"Codice\s+d.urgenza",
            r"Data/Ora\s+di\s+Triage",
            r"Data/Ora\s+di\s+chiusura",
            r"Data/Ora\s+presa\s+in\s+carico",
            r"Cartella\s+Clinica\s+di\s+P\.S\.",
            # Footer universali
            r"^\d+/\d+$",
            r"Pagina\s+\d+\s+di\s+\d+",
            r"Pag\s+\d+\s+di\s+\d+",
            r"TSRM:",
            r"^Coord\.\s+TSRM",
            r"Il\s+Medico$",
            r"Il\s+Medico\s+Specialista",
            r"\(Timbro\s+e\s+Firma\)",
            r"Validato\s+in\s+Data",
            r"Firmato\s+in\s+Data",
            r"Data\s+Stesura:",
            r"Verbale\s+N.\s+\d+",
            r"Versione\s+N.\s+\d+",
            r"[A-Z]+\s+[A-Z]+\s+\([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\)",
            r"[A-Z]+,\s+l.\s+\d{2}/\d{2}/\d{4}",
            r"non\s+vale\s+come\s+documentazione",
            r"RICETTA\s+IN\s+COPIA",
            *_COMMON_PII,
            *_COMMON_FOOTER,
        ],
        keep_patterns=[
            r"Medico\s+Radiologo:",
            r"Medico\s+refertante:",
            r"ANAMNESI",
            r"DIAGNOSI",
            r"CONCLUSIONI",
            r"TERAPIA",
            r"Terapia\s+in\s+atto",
            r"mg",
            r"gtt",
            r"Egregio\s+Collega",
        ],
        newline_before_patterns=[
            r"Diagnosi\s+alla\s+dimissione",
            r"Motivo\s+del\s+ricovero",
            r"Decorso\s+clinico",
            r"Terapia\b",
            r"CONCLUSIONI",
            r"ANAMNESI",
        ],
    )


# ---------------------------------------------------------------------------
# ProfileManager
# ---------------------------------------------------------------------------

class ProfileManager:
    """Manages built-in and custom report profiles."""

    _profiles: list[ReportProfile] = []
    _default: ReportProfile | None = None
    _initialized: bool = False

    @classmethod
    def initialize(cls) -> None:
        """Load built-in profiles. Safe to call multiple times.

        Profiles are ordered from most specific to least specific
        so that find_profile() returns the best match.
        """
        if cls._initialized:
            return
        cls._profiles = [
            # Most specific first (more identifier patterns)
            _create_tsa_maugeri(),
            _create_rx_maugeri(),
            _create_spec_asst_radiologia(),
            _create_spec_pneumologia(),
            _create_anatpat(),
            _create_spec_strapparava(),
            _create_spec_richiedei(),
            # Hospital-specific (single identifier)
            _create_ps_asst(),
            _create_spec_maugeri_cardio(),
            _create_lab_synlab(),
            _create_lab_bianalisi(),
            # Broader matchers (need 2 identifiers)
            _create_dimosp_asst(),
            _create_dimosp_maugeri(),
            _create_spec_asst(),
        ]
        cls._default = _create_default()
        cls._initialized = True

    @classmethod
    def find_profile(cls, text: str) -> ReportProfile:
        """Find the best matching profile for the given text.

        All identifier_patterns of a profile must appear in the text
        (case-insensitive substring match). Returns the default profile
        if no specific match is found.
        """
        cls.initialize()
        lower_text = text.lower()
        for profile in cls._profiles:
            if profile.identifier_patterns and all(
                p.lower() in lower_text for p in profile.identifier_patterns
            ):
                return profile
        return cls.get_default()

    @classmethod
    def get_default(cls) -> ReportProfile:
        """Return the generic default profile."""
        cls.initialize()
        assert cls._default is not None
        return cls._default

    @classmethod
    def get_profiles(cls) -> list[ReportProfile]:
        """Return all registered profiles (excluding default)."""
        cls.initialize()
        return list(cls._profiles)

    @classmethod
    def load_custom_profile(cls, path: Path) -> ReportProfile | None:
        """Load a custom profile from a JSON file.

        JSON format::

            {
                "name": "...",
                "identifier_patterns": ["..."],
                "patient_name_patterns": ["..."],
                "exclude_patterns": ["..."],
                "keep_patterns": ["..."],
                "newline_before_patterns": ["..."]
            }
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            profile = ReportProfile(
                name=data.get("name", path.stem),
                identifier_patterns=data.get("identifier_patterns", []),
                patient_name_patterns=data.get("patient_name_patterns", []),
                exclude_patterns=data.get("exclude_patterns", []),
                keep_patterns=data.get("keep_patterns", []),
                newline_before_patterns=data.get("newline_before_patterns", []),
            )
            cls.initialize()
            cls._profiles.append(profile)
            logger.info("Profilo custom caricato: %s", profile.name)
            return profile
        except Exception as e:
            logger.error("Errore caricamento profilo %s: %s", path, e)
            return None
