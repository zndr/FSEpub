param(
    [Parameter(Mandatory=$true)]
    [string]$MappingFile,

    [string]$SourceDir = ".\downloads"
)

if (-not (Test-Path $MappingFile)) {
    Write-Error "File mapping non trovato: $MappingFile"
    exit 1
}

$mappings = Get-Content $MappingFile -Raw -Encoding UTF8 | ConvertFrom-Json
$renamed = 0
$skipped = 0
$errors = 0

foreach ($entry in $mappings) {
    if ($entry.renamed -eq $true) {
        $skipped++
        continue
    }

    $src = Join-Path $SourceDir $entry.original_filename
    $dst = Join-Path $SourceDir $entry.renamed_filename

    if (-not (Test-Path $src)) {
        Write-Host "  SKIP: $($entry.original_filename) non trovato"
        $skipped++
        continue
    }

    if (Test-Path $dst) {
        Write-Host "  SKIP: $($entry.renamed_filename) esiste gia"
        $skipped++
        continue
    }

    try {
        Rename-Item -Path $src -NewName (Split-Path $dst -Leaf) -ErrorAction Stop
        Write-Host "  OK: $($entry.original_filename) -> $($entry.renamed_filename)"
        $renamed++
    } catch {
        Write-Host "  ERRORE: $($entry.original_filename): $_"
        $errors++
    }
}

Write-Host "`nRiepilogo: $renamed rinominati, $skipped saltati, $errors errori"
