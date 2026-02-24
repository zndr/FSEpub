"""Report parsing profiles for Italian medical documents.

Ported from MedicalReportMonitor C++ (ReportProfile.cpp).
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
# Built-in profiles (ported from ReportProfile.cpp)
# ---------------------------------------------------------------------------

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
            r"Paziente:\s+([A-Z]+\s+[A-Z]+)\s+Anni:",
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
            r"Lumezzane,\s+\d{2}/\d{2}/\d{4}",
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
    """Generic default profile for any Italian medical report."""
    return ReportProfile(
        name="default",
        identifier_patterns=[],
        patient_name_patterns=[
            r"Sig\./Sig\.ra:\s+([A-Za-z][A-Za-z\s]+?)(?:\s{2,}|ID)",
            r"Sig\.\s+([A-Za-z][A-Za-z\s]+?)(?:\s{2,}|ID)",
            r"Paziente:\s+([A-Za-z][A-Za-z\s]+?)(?:\s{2,}|Data)",
        ],
        exclude_patterns=[
            r"Istituto",
            r"IRCCS",
            r"ASST",
            r"Ospedale",
            r"Servizio\s+di",
            r"Primario:",
            r"Direttore:",
            r"Tel\.",
            r"Fax\.",
            r"Email:",
            r"Sig\./Sig\.ra:",
            r"Sig\.\s+[A-Z]",
            r"Data\s+di\s+Nascita:",
            r"Data\s+nascita:",
            r"\d{2}/\d{2}/\d{4}",
            r"Codice\s+Fiscale:",
            r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]",
            r"ID\s+Paziente:",
            r"N\.\s+di\s+accesso:",
            r"\d{10}",
            r"Provenienza:",
            r"Prestazione:",
            r"Schedulazione:",
            r"Esecuzione:",
            r"Data\s+validazione",
            r"Documento\s+informatico",
            r"stampa\s+costituisce",
            r"D\.Lgs",
            r"Pag\s+\d+\s+di\s+\d+",
            r"TSRM:",
            r"Tecnico:",
        ],
        keep_patterns=[
            r"Medico\s+Radiologo:",
            r"Medico\s+refertante:",
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
        """Load built-in profiles. Safe to call multiple times."""
        if cls._initialized:
            return
        cls._profiles = [
            _create_rx_maugeri(),
            _create_tsa_maugeri(),
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
            if all(p.lower() in lower_text for p in profile.identifier_patterns):
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
