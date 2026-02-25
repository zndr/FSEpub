"""LLM integration for structured medical report analysis.

Supports multiple providers (Claude, OpenAI/ChatGPT, Gemini, Claude CLI,
and any OpenAI-compatible custom endpoint). The medical prompt is
provider-agnostic and ported from MedicalReportMonitor's ClaudeAnalyzer.

IMPORTANT: Only ANONYMIZED text (with PII already removed by
TextAnonymizer) should be sent to any external LLM.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Default models per provider
DEFAULT_MODELS: dict[str, str] = {
    "claude_api": "claude-sonnet-4-6",
    "openai_api": "gpt-4o",
    "gemini_api": "gemini-2.0-flash",
    "mistral_api": "mistral-large-latest",
    "claude_cli": "",
    "custom_url": "",
}

# Human-readable provider names
PROVIDER_LABELS: dict[str, str] = {
    "claude_api": "Claude (Anthropic)",
    "openai_api": "ChatGPT (OpenAI)",
    "gemini_api": "Gemini (Google)",
    "mistral_api": "Mistral",
    "claude_cli": "Claude CLI (locale)",
    "custom_url": "Endpoint personalizzato",
}

# Reverse mapping: label -> provider key
LABEL_TO_PROVIDER: dict[str, str] = {v: k for k, v in PROVIDER_LABELS.items()}


@dataclass
class LLMConfig:
    """Configuration for an LLM provider."""

    provider: str = ""          # claude_api, openai_api, gemini_api, mistral_api, claude_cli, custom_url
    api_key: str = ""           # Decrypted API key
    model: str = ""             # Model identifier
    timeout: int = 120          # Seconds
    base_url: str = ""          # For custom_url provider


# ---------------------------------------------------------------------------
# Medical analysis prompt (ported from ClaudeAnalyzer.cpp BuildPrompt)
# ---------------------------------------------------------------------------

MEDICAL_PROMPT = """\
Sei un assistente medico specializzato nell'analisi di referti medici italiani.
Analizza il seguente testo estratto da un referto medico PDF e produci un output strutturato.

## Regole di filtraggio

ESCLUDI: header istituzionale (logo, nome ospedale, indirizzo, recapiti), \
footer (note legali, firma digitale, numerazione pagine, privacy), \
dati anagrafici paziente (nome, cognome, data nascita, codice fiscale, tessera sanitaria, ID paziente, indirizzo), \
metadati amministrativi (provenienza, numero ricovero, numero nosologico, codice impegnativa, medico prescrittore), \
quesito diagnostico/clinico, codici di classificazione (DRG, ICD-9/ICD-10, codici nomenclatore).

INCLUDI: corpo del referto (descrizione esame, reperti, diagnosi, decorso clinico), \
conclusioni diagnostiche e raccomandazioni follow-up, \
terapia prescritta con posologia dettagliata, \
procedure eseguite e risultati.

## Classificazione reperti patologici

(+++) Urgente/critico - Richiede attenzione immediata (neoplasia sospetta, embolia, stenosi critica, frattura instabile, pneumotorace)
(++) Da monitorare - Anomalia che richiede follow-up (nodulo < 1cm, lieve ipertrofia, diverticolosi, ernia discale senza deficit)
(+) Incidentale/minore - Reperto anormale ma scarsa rilevanza clinica immediata (cisti semplici, calcificazioni minime, lipoma, osteofitosi lieve)

Formato reperto: (severita) Reperto sintetico - "Citazione dal testo originale"

## Formato output OBBLIGATORIO

REPERTI PATOLOGICI SIGNIFICATIVI
[lista reperti ordinati: (+++) prima, poi (++), poi (+)]
[Se nessun reperto patologico: "Nessun reperto patologico significativo rilevato."]

________________________________________________________________________________

TESTO COMPLETO DEL REFERTO
[corpo del referto estratto, testo pulito]

________________________________________________________________________________

Data referto: [GG/MM/AAAA]
Medico: [Titolo Nome Cognome]

## Regole di formattazione

- Testo continuo: unire le righe spezzate dal layout PDF
- A capo solo dopo punto fermo o tra sezioni logiche
- Preservare struttura logica originale (sezioni, paragrafi, sottotitoli)
- Nessun markdown nel corpo del referto: solo testo pulito
- Preservare valori numerici esattamente come riportati
- Elenchi farmacologici: un farmaco per riga con posologia

## Lettera di dimissione

Se il referto e' una lettera di dimissione, preservare le sezioni nell'ordine: \
diagnosi alla dimissione, motivo del ricovero, anamnesi rilevante, decorso clinico, \
esami diagnostici, procedure/interventi, consulenze, condizioni alla dimissione, \
terapia alla dimissione (un farmaco per riga), indicazioni al follow-up.

---

TESTO DEL REFERTO DA ANALIZZARE:

"""


class LLMAnalyzer:
    """Analyzes anonymized medical reports via an external LLM."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def analyze(self, anonymized_text: str) -> str:
        """Send anonymized text to the configured LLM and return structured output.

        Args:
            anonymized_text: Report text with all PII already removed.

        Returns:
            LLM response text, or empty string on failure.
        """
        if not anonymized_text.strip():
            logger.warning("Testo vuoto, analisi AI saltata")
            return ""

        provider = self.config.provider
        try:
            if provider == "claude_api":
                return self._call_claude_api(anonymized_text)
            elif provider == "openai_api":
                return self._call_openai_api(anonymized_text)
            elif provider == "gemini_api":
                return self._call_gemini_api(anonymized_text)
            elif provider == "mistral_api":
                return self._call_mistral_api(anonymized_text)
            elif provider == "claude_cli":
                return self._call_claude_cli(anonymized_text)
            elif provider == "custom_url":
                return self._call_custom_endpoint(anonymized_text)
            else:
                logger.error("Provider LLM sconosciuto: %s", provider)
                return ""
        except Exception as e:
            logger.error("Errore analisi AI (%s): %s", provider, e)
            return ""

    def is_available(self) -> bool:
        """Check if the configured provider is reachable.

        Returns:
            True if the provider responds successfully.
        """
        provider = self.config.provider
        try:
            if provider == "claude_api":
                return self._test_claude_api()
            elif provider == "openai_api":
                return self._test_openai_api()
            elif provider == "gemini_api":
                return self._test_gemini_api()
            elif provider == "mistral_api":
                return self._test_mistral_api()
            elif provider == "claude_cli":
                return self._test_claude_cli()
            elif provider == "custom_url":
                return self._test_custom_endpoint()
            return False
        except Exception as e:
            logger.debug("Test provider %s fallito: %s", provider, e)
            return False

    # ------------------------------------------------------------------
    # Claude API (Anthropic)
    # ------------------------------------------------------------------

    def _call_claude_api(self, text: str) -> str:
        model = self.config.model or DEFAULT_MODELS["claude_api"]
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 8192,
                "messages": [{"role": "user", "content": MEDICAL_PROMPT + text}],
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return _extract_text_from_anthropic(data)

    def _test_claude_api(self) -> bool:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.config.model or DEFAULT_MODELS["claude_api"],
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "Rispondi solo OK."}],
            },
            timeout=15,
        )
        return resp.status_code == 200

    # ------------------------------------------------------------------
    # OpenAI API (ChatGPT)
    # ------------------------------------------------------------------

    def _call_openai_api(self, text: str) -> str:
        model = self.config.model or DEFAULT_MODELS["openai_api"]
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 8192,
                "messages": [
                    {"role": "system", "content": "Sei un assistente medico specializzato."},
                    {"role": "user", "content": MEDICAL_PROMPT + text},
                ],
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return _extract_text_from_openai(data)

    def _test_openai_api(self) -> bool:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model or DEFAULT_MODELS["openai_api"],
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "Rispondi solo OK."}],
            },
            timeout=15,
        )
        return resp.status_code == 200

    # ------------------------------------------------------------------
    # Gemini API (Google)
    # ------------------------------------------------------------------

    def _call_gemini_api(self, text: str) -> str:
        model = self.config.model or DEFAULT_MODELS["gemini_api"]
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={self.config.api_key}"
        )
        resp = httpx.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": MEDICAL_PROMPT + text}]}],
                "generationConfig": {"maxOutputTokens": 8192},
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return _extract_text_from_gemini(data)

    def _test_gemini_api(self) -> bool:
        model = self.config.model or DEFAULT_MODELS["gemini_api"]
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={self.config.api_key}"
        )
        resp = httpx.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": "Rispondi solo OK."}]}]},
            timeout=15,
        )
        return resp.status_code == 200

    # ------------------------------------------------------------------
    # Claude CLI (local subprocess, like MedicalReportMonitor)
    # ------------------------------------------------------------------

    def _call_claude_cli(self, text: str) -> str:
        prompt = MEDICAL_PROMPT + text

        # Write prompt to temp file (handles large texts)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as f:
            f.write(prompt)
            prompt_file = f.name

        output_file = tempfile.mktemp(suffix=".txt")
        try:
            cmd = f'cmd /c type "{prompt_file}" | claude --print > "{output_file}"'
            result = subprocess.run(
                cmd, shell=True, timeout=self.config.timeout,
                capture_output=True, creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            if result.returncode != 0:
                logger.error("Claude CLI errore (exit %d)", result.returncode)
                return ""

            output_path = Path(output_file)
            if not output_path.exists():
                return ""
            content = output_path.read_text(encoding="utf-8").strip()
            if len(content) < 50:
                logger.warning("Output Claude CLI troppo breve (%d caratteri)", len(content))
                return ""
            return content
        except subprocess.TimeoutExpired:
            logger.error("Timeout Claude CLI (%ds)", self.config.timeout)
            return ""
        finally:
            Path(prompt_file).unlink(missing_ok=True)
            Path(output_file).unlink(missing_ok=True)

    def _test_claude_cli(self) -> bool:
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True, timeout=10,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    # ------------------------------------------------------------------
    # Mistral API
    # ------------------------------------------------------------------

    def _call_mistral_api(self, text: str) -> str:
        model = self.config.model or DEFAULT_MODELS["mistral_api"]
        resp = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 8192,
                "messages": [
                    {"role": "system", "content": "Sei un assistente medico specializzato."},
                    {"role": "user", "content": MEDICAL_PROMPT + text},
                ],
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return _extract_text_from_openai(data)

    def _test_mistral_api(self) -> bool:
        resp = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model or DEFAULT_MODELS["mistral_api"],
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "Rispondi solo OK."}],
            },
            timeout=15,
        )
        return resp.status_code == 200

    # ------------------------------------------------------------------
    # Custom OpenAI-compatible endpoint
    # ------------------------------------------------------------------

    def _call_custom_endpoint(self, text: str) -> str:
        base = self.config.base_url.rstrip("/")
        model = self.config.model
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        resp = httpx.post(
            f"{base}/v1/chat/completions",
            headers=headers,
            json={
                "model": model,
                "max_tokens": 8192,
                "messages": [{"role": "user", "content": MEDICAL_PROMPT + text}],
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return _extract_text_from_openai(data)

    def _test_custom_endpoint(self) -> bool:
        if not self.config.base_url:
            return False
        base = self.config.base_url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        resp = httpx.post(
            f"{base}/v1/chat/completions",
            headers=headers,
            json={
                "model": self.config.model or "test",
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "Rispondi solo OK."}],
            },
            timeout=15,
        )
        return resp.status_code == 200


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

def _extract_text_from_anthropic(data: dict) -> str:
    """Extract text from Anthropic Messages API response."""
    for block in data.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _extract_text_from_openai(data: dict) -> str:
    """Extract text from OpenAI Chat Completions API response."""
    choices = data.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return ""


def _extract_text_from_gemini(data: dict) -> str:
    """Extract text from Gemini generateContent API response."""
    candidates = data.get("candidates", [])
    if candidates:
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if parts:
            return parts[0].get("text", "")
    return ""
