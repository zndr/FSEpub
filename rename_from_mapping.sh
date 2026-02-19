#!/usr/bin/env bash
# Standalone script: rinomina file usando un mapping JSON (richiede jq)
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Uso: $0 <mapping.json> [source_dir]"
    exit 1
fi

MAPPING_FILE="$1"
SOURCE_DIR="${2:-./downloads}"

if [ ! -f "$MAPPING_FILE" ]; then
    echo "Errore: file mapping non trovato: $MAPPING_FILE"
    exit 1
fi

if ! command -v jq &>/dev/null; then
    echo "Errore: jq non installato (apt install jq / brew install jq)"
    exit 1
fi

renamed=0
skipped=0
errors=0
count=$(jq length "$MAPPING_FILE")

for i in $(seq 0 $((count - 1))); do
    already_renamed=$(jq -r ".[$i].renamed" "$MAPPING_FILE")
    if [ "$already_renamed" = "true" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    original=$(jq -r ".[$i].original_filename" "$MAPPING_FILE")
    target=$(jq -r ".[$i].renamed_filename" "$MAPPING_FILE")
    src="$SOURCE_DIR/$original"
    dst="$SOURCE_DIR/$target"

    if [ ! -f "$src" ]; then
        echo "  SKIP: $original non trovato"
        skipped=$((skipped + 1))
        continue
    fi

    if [ -f "$dst" ]; then
        echo "  SKIP: $target esiste gia"
        skipped=$((skipped + 1))
        continue
    fi

    if mv "$src" "$dst"; then
        echo "  OK: $original -> $target"
        renamed=$((renamed + 1))
    else
        echo "  ERRORE: $original"
        errors=$((errors + 1))
    fi
done

echo ""
echo "Riepilogo: $renamed rinominati, $skipped saltati, $errors errori"
