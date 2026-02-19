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
    def build_filename(patient_name: str, codice_fiscale: str, disciplina: str) -> str:
        safe_name = re.sub(r'[<>:"/\\|?*]', "", patient_name).replace(" ", "_")
        safe_disciplina = re.sub(r'[<>:"/\\|?*]', "", disciplina).replace(" ", "_")
        return f"{safe_name}_{codice_fiscale}_{safe_disciplina}.pdf"

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
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}_{counter}{suffix}"
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
