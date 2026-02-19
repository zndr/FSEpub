#!/usr/bin/env python3
"""Standalone script: rinomina file usando un mapping JSON generato da main.py."""

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python rename_from_mapping.py <mapping.json> [source_dir]")
        sys.exit(1)

    mapping_file = Path(sys.argv[1])
    source_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("./downloads")

    if not mapping_file.exists():
        print(f"Errore: file mapping non trovato: {mapping_file}")
        sys.exit(1)

    mappings = json.loads(mapping_file.read_text(encoding="utf-8"))
    renamed = 0
    skipped = 0
    errors = 0

    for entry in mappings:
        if entry.get("renamed", False):
            skipped += 1
            continue

        src = source_dir / entry["original_filename"]
        dst = source_dir / entry["renamed_filename"]

        if not src.exists():
            print(f"  SKIP: {src.name} non trovato")
            skipped += 1
            continue

        if dst.exists():
            print(f"  SKIP: {dst.name} esiste già")
            skipped += 1
            continue

        try:
            src.rename(dst)
            print(f"  OK: {src.name} -> {dst.name}")
            renamed += 1
        except OSError as e:
            print(f"  ERRORE: {src.name}: {e}")
            errors += 1

    print(f"\nRiepilogo: {renamed} rinominati, {skipped} saltati, {errors} errori")


if __name__ == "__main__":
    main()
