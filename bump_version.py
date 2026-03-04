"""Auto-increment patch version across project files.

Called by build.bat before each build. Prints the new version to stdout
so the caller can capture it.

Usage:
    python bump_version.py              # bump patch, check compat, print new version
    python bump_version.py --current    # print current version without bumping
    python bump_version.py --hash FILE  # update version.json with SHA256 and size of FILE
    python bump_version.py --compat     # run retrocompatibility check only
"""

import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
VERSION_PY = ROOT / "version.py"
INSTALLER_ISS = ROOT / "installer.iss"
VERSION_JSON = ROOT / "version.json"
GUI_PY = ROOT / "gui.py"
CONFIG_PY = ROOT / "config.py"


def get_version() -> str:
    m = re.search(r'__version__ = "(\d+\.\d+\.\d+)"', VERSION_PY.read_text())
    if not m:
        raise SystemExit("Cannot parse version from version.py")
    return m.group(1)


def bump_patch(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def update_files(new_ver: str) -> None:
    today = date.today().isoformat()

    # version.py
    VERSION_PY.write_text(re.sub(
        r'__version__ = "[\d.]+"',
        f'__version__ = "{new_ver}"',
        VERSION_PY.read_text(),
    ))

    # installer.iss
    INSTALLER_ISS.write_text(re.sub(
        r'#define MyAppVersion "[\d.]+"',
        f'#define MyAppVersion "{new_ver}"',
        INSTALLER_ISS.read_text(),
    ))

    # version.json
    data = json.loads(VERSION_JSON.read_text())
    old_ver = data["Version"]
    data["Version"] = new_ver
    data["DownloadUrl"] = data["DownloadUrl"].replace(old_ver, new_ver)
    data["ReleaseDate"] = today
    data["Sha256Hash"] = ""
    data["FileSize"] = 0
    VERSION_JSON.write_text(json.dumps(data, indent=4) + "\n")


# ---------------------------------------------------------------------------
#  Retrocompatibility verification
# ---------------------------------------------------------------------------

def _get_latest_tag() -> str | None:
    """Get the most recent git tag by version sort."""
    try:
        result = subprocess.run(
            ["git", "tag", "--sort=-version:refname"],
            capture_output=True, text=True, timeout=5, cwd=ROOT,
        )
        tags = result.stdout.strip().splitlines()
        return tags[0] if tags else None
    except Exception:
        return None


def _get_file_from_tag(tag: str, filepath: str) -> str | None:
    """Get file contents from a git tag. Returns None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "show", f"{tag}:{filepath}"],
            capture_output=True, text=True, timeout=5, cwd=ROOT,
        )
        return result.stdout if result.returncode == 0 else None
    except Exception:
        return None


def _parse_settings_spec(source: str) -> dict[str, tuple[str, str, str]]:
    """Parse SETTINGS_SPEC from gui.py source.

    Returns {key: (label, default, kind)}.
    """
    m = re.search(r"SETTINGS_SPEC\s*=\s*\[(.*?)\]", source, re.DOTALL)
    if not m:
        return {}
    entries = re.findall(
        r'\(\s*"(\w+)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"(\w+)"\s*\)',
        m.group(1),
    )
    return {key: (label, default, kind) for key, label, default, kind in entries}


def _parse_config_fields(source: str) -> set[str]:
    """Parse field names from Config dataclass in config.py."""
    m = re.search(r"class Config:.*?(?=\n    @|\nclass |\Z)", source, re.DOTALL)
    if not m:
        return set()
    fields = re.findall(r"^\s{4}(\w+)\s*:", m.group(0), re.MULTILINE)
    return set(fields)


def check_retrocompat(tag: str | None = None) -> tuple[list[str], list[str]]:
    """Compare current settings schema against a released tag.

    Returns (errors, warnings). Errors = breaking changes that block the build.
    """
    if tag is None:
        tag = _get_latest_tag()
    if not tag:
        return ([], ["Nessun tag git trovato, skip verifica retrocompatibilita'"])

    errors: list[str] = []
    warnings: list[str] = []

    # --- SETTINGS_SPEC (gui.py) ---
    old_gui = _get_file_from_tag(tag, "gui.py")
    new_gui = GUI_PY.read_text(encoding="utf-8")

    if old_gui:
        old_spec = _parse_settings_spec(old_gui)
        new_spec = _parse_settings_spec(new_gui)

        removed = set(old_spec) - set(new_spec)
        added = set(new_spec) - set(old_spec)
        common = set(old_spec) & set(new_spec)

        for key in sorted(removed):
            errors.append(
                f"SETTINGS_SPEC: chiave '{key}' rimossa (presente in {tag}). "
                f"Il settings.env degli utenti conterra' questa chiave orfana."
            )

        for key in sorted(added):
            _, default, kind = new_spec[key]
            if default == "" and kind not in ("text", "password", "dir"):
                warnings.append(
                    f"SETTINGS_SPEC: nuova chiave '{key}' (tipo {kind}) "
                    f"con default vuoto — verificare che sia intenzionale."
                )

        for key in sorted(common):
            old_label, old_default, old_kind = old_spec[key]
            new_label, new_default, new_kind = new_spec[key]
            if old_kind != new_kind:
                errors.append(
                    f"SETTINGS_SPEC: tipo di '{key}' cambiato "
                    f"da '{old_kind}' a '{new_kind}'."
                )
            if old_default != new_default:
                warnings.append(
                    f"SETTINGS_SPEC: default di '{key}' cambiato "
                    f"da '{old_default}' a '{new_default}'. "
                    f"Gli utenti esistenti non vedranno il nuovo default."
                )
    else:
        warnings.append(f"gui.py non trovato nel tag {tag}, skip verifica SETTINGS_SPEC")

    # --- Config dataclass (config.py) ---
    old_config = _get_file_from_tag(tag, "config.py")
    new_config = CONFIG_PY.read_text(encoding="utf-8")

    if old_config:
        old_fields = _parse_config_fields(old_config)
        new_fields = _parse_config_fields(new_config)

        removed_fields = old_fields - new_fields
        for field in sorted(removed_fields):
            errors.append(
                f"Config dataclass: campo '{field}' rimosso (presente in {tag}). "
                f"Codice che referenzia config.{field} crashera'."
            )
    else:
        warnings.append(f"config.py non trovato nel tag {tag}, skip verifica Config")

    return errors, warnings


def print_compat_report(tag: str | None = None) -> bool:
    """Print retrocompatibility report. Returns True if compatible (no errors)."""
    if tag is None:
        tag = _get_latest_tag()

    print(f"[Retrocompatibilita'] Confronto con {tag or '(nessun tag)'}...",
          file=sys.stderr)

    errors, warnings = check_retrocompat(tag)

    for w in warnings:
        print(f"  AVVISO: {w}", file=sys.stderr)
    for e in errors:
        print(f"  ERRORE: {e}", file=sys.stderr)

    if errors:
        print(f"\n  {len(errors)} errore/i di retrocompatibilita' trovato/i.",
              file=sys.stderr)
        print("  Correggi gli errori o usa --skip-compat per forzare il build.",
              file=sys.stderr)
        return False

    status = "compatibile" if not warnings else f"compatibile con {len(warnings)} avviso/i"
    print(f"  Risultato: {status}", file=sys.stderr)
    return True


def update_hash(filepath: str) -> None:
    p = Path(filepath)
    if not p.exists():
        raise SystemExit(f"File not found: {filepath}")

    sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
    size = p.stat().st_size

    data = json.loads(VERSION_JSON.read_text())
    data["Sha256Hash"] = sha256
    data["FileSize"] = size
    VERSION_JSON.write_text(json.dumps(data, indent=4) + "\n")

    print(f"{sha256} {size}")


if __name__ == "__main__":
    if "--current" in sys.argv:
        print(get_version())
    elif "--hash" in sys.argv:
        idx = sys.argv.index("--hash")
        if idx + 1 >= len(sys.argv):
            raise SystemExit("Usage: bump_version.py --hash FILE")
        update_hash(sys.argv[idx + 1])
    elif "--compat" in sys.argv:
        ok = print_compat_report()
        sys.exit(0 if ok else 1)
    else:
        old = get_version()
        new = bump_patch(old)
        update_files(new)
        # Retrocompatibility check (skip with --skip-compat)
        if "--skip-compat" not in sys.argv:
            if not print_compat_report():
                raise SystemExit(1)
        print(new)
