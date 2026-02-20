import json
import re
from datetime import datetime
from pathlib import Path

from config import Config
from logger_module import ProcessingLogger


class FileManager:
    INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')

    def __init__(self, config: Config, logger: ProcessingLogger) -> None:
        self._config = config
        self._logger = logger
        self._mappings: list[dict] = []

    @staticmethod
    def _tipologia_tag(tipologia: str) -> str:
        upper = tipologia.strip().upper()
        if "LABORATORIO" in upper:
            return "LAB"
        if "PRONTO SOCCORSO" in upper:
            return "PS"
        if upper.startswith("REFERTO"):
            return "SPEC"
        return "DOC"

    @staticmethod
    def build_filename(patient_name: str, codice_fiscale: str, disciplina: str) -> str:
        safe_name = re.sub(r'[<>:"/\\|?*]', "", patient_name).replace(" ", "_")
        tag = FileManager._tipologia_tag(disciplina)
        return f"{codice_fiscale}_{safe_name}_{tag}.pdf"

    def rename_download(
        self,
        download_path: Path,
        patient_name: str,
        codice_fiscale: str,
        disciplina: str,
        fse_link: str,
    ) -> Path | None:
        new_name = self.build_filename(patient_name, codice_fiscale, disciplina)
        dest = self._config.download_dir / new_name
        dest = self._resolve_collision(dest)

        try:
            download_path.rename(dest)
            self._logger.info(f"Rinominato: {download_path.name} -> {dest.name}")
            self._add_mapping(
                original=download_path.name,
                renamed=dest.name,
                patient_name=patient_name,
                codice_fiscale=codice_fiscale,
                disciplina=disciplina,
                fse_link=fse_link,
                renamed_ok=True,
            )
            return dest
        except OSError as e:
            self._logger.error(f"Errore rinomina {download_path.name}: {e}")
            self._add_mapping(
                original=download_path.name,
                renamed=new_name,
                patient_name=patient_name,
                codice_fiscale=codice_fiscale,
                disciplina=disciplina,
                fse_link=fse_link,
                renamed_ok=False,
            )
            return None

    def save_mappings(self) -> Path | None:
        if not self._mappings:
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mapping_file = self._config.log_dir / f"mapping_{timestamp}.json"
        mapping_file.write_text(
            json.dumps(self._mappings, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._logger.info(f"Mapping salvato in {mapping_file}")
        return mapping_file

    @staticmethod
    def _resolve_collision(path: Path) -> Path:
        if not path.exists():
            return path
        # Insert counter before the tag (last _ segment before .pdf)
        # e.g. CF_NOME_LAB.pdf -> CF_NOME_1_LAB.pdf
        stem = path.stem          # CF_NOME_LAB
        suffix = path.suffix      # .pdf
        parent = path.parent
        parts = stem.rsplit("_", 1)  # ["CF_NOME", "LAB"]
        base = parts[0]
        tag = parts[1] if len(parts) > 1 else ""
        counter = 1
        while True:
            candidate = parent / f"{base}_{counter}_{tag}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _add_mapping(
        self,
        original: str,
        renamed: str,
        patient_name: str,
        codice_fiscale: str,
        disciplina: str,
        fse_link: str,
        renamed_ok: bool,
    ) -> None:
        self._mappings.append({
            "original_filename": original,
            "renamed_filename": renamed,
            "patient_name": patient_name,
            "codice_fiscale": codice_fiscale,
            "disciplina": disciplina,
            "fse_link": fse_link,
            "download_timestamp": datetime.now().isoformat(),
            "renamed": renamed_ok,
        })
