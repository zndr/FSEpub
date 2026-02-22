"""Auto-increment patch version across project files.

Called by build.bat before each build. Prints the new version to stdout
so the caller can capture it.

Usage:
    python bump_version.py              # bump patch and print new version
    python bump_version.py --current    # print current version without bumping
    python bump_version.py --hash FILE  # update version.json with SHA256 and size of FILE
"""

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
VERSION_PY = ROOT / "version.py"
INSTALLER_ISS = ROOT / "installer.iss"
VERSION_JSON = ROOT / "version.json"


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
    else:
        old = get_version()
        new = bump_patch(old)
        update_files(new)
        print(new)
