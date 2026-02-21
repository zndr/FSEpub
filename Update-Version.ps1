#Requires -Version 5.1

<#
.SYNOPSIS
    Aggiorna la versione di FSE Processor in modo centralizzato

.DESCRIPTION
    Questo script aggiorna automaticamente tutti i riferimenti di versione nel progetto:
    - version.py (versione Python - source of truth)
    - installer.iss (versione installer Inno Setup)
    - build.bat (nome file output)
    - version.json (metadati release per auto-update)
    - CHANGELOG.md (aggiunge sezione per la nuova versione)

.PARAMETER NewVersion
    La nuova versione in formato X.Y.Z (es. "1.1.0")

.PARAMETER ReleaseDate
    Data di rilascio in formato "yyyy-MM-dd" (default: data odierna)

.EXAMPLE
    .\Update-Version.ps1 -NewVersion "1.1.0"
    Aggiorna alla versione 1.1.0 con la data odierna

.EXAMPLE
    .\Update-Version.ps1 -NewVersion "1.2.0" -ReleaseDate "2026-03-15"
    Aggiorna alla versione 1.2.0 con data specifica
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$NewVersion,

    [Parameter(Mandatory = $false)]
    [string]$ReleaseDate = ""
)

# Colori per output
$colorSuccess = "Green"
$colorWarning = "Yellow"
$colorError = "Red"
$colorInfo = "Cyan"

Write-Host ""
Write-Host "=== Aggiornamento Versione FSE Processor ===" -ForegroundColor $colorInfo
Write-Host ""

# Valida formato versione (X.Y.Z)
if ($NewVersion -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "X Formato versione non valido: $NewVersion" -ForegroundColor $colorError
    Write-Host "   Usa il formato X.Y.Z (es. 1.1.0)" -ForegroundColor $colorWarning
    exit 1
}

# Data di rilascio
if ([string]::IsNullOrWhiteSpace($ReleaseDate)) {
    $ReleaseDate = (Get-Date).ToString("yyyy-MM-dd")
}

$ReleaseDateDisplay = (Get-Date $ReleaseDate).ToString("dd MMMM yyyy", [System.Globalization.CultureInfo]::CreateSpecificCulture("it-IT"))

Write-Host "Nuova versione: $NewVersion" -ForegroundColor $colorInfo
Write-Host "Data rilascio:  $ReleaseDate ($ReleaseDateDisplay)" -ForegroundColor $colorInfo
Write-Host ""

$updatedCount = 0

# 1. version.py (source of truth)
Write-Host "1. Aggiornamento version.py..." -ForegroundColor $colorInfo
$versionPyPath = Join-Path $PSScriptRoot "version.py"
if (Test-Path $versionPyPath) {
    $content = Get-Content $versionPyPath -Raw
    $content = $content -replace '__version__ = "[\d\.]+"', "__version__ = `"$NewVersion`""
    $content | Set-Content $versionPyPath -NoNewline
    Write-Host "   OK version.py aggiornato" -ForegroundColor $colorSuccess
    $updatedCount++
} else {
    Write-Host "   ATTENZIONE: version.py non trovato" -ForegroundColor $colorWarning
}

# 2. installer.iss
Write-Host "2. Aggiornamento installer.iss..." -ForegroundColor $colorInfo
$issPath = Join-Path $PSScriptRoot "installer.iss"
if (Test-Path $issPath) {
    $content = Get-Content $issPath -Raw
    $content = $content -replace '#define MyAppVersion "[\d\.]+"', "#define MyAppVersion `"$NewVersion`""
    $content | Set-Content $issPath -NoNewline
    Write-Host "   OK installer.iss aggiornato" -ForegroundColor $colorSuccess
    $updatedCount++
} else {
    Write-Host "   ATTENZIONE: installer.iss non trovato" -ForegroundColor $colorWarning
}

# 3. build.bat (riga output finale)
Write-Host "3. Aggiornamento build.bat..." -ForegroundColor $colorInfo
$buildBatPath = Join-Path $PSScriptRoot "build.bat"
if (Test-Path $buildBatPath) {
    $content = Get-Content $buildBatPath -Raw
    $content = $content -replace 'FSE_Processor_Setup_[\d\.]+(_QT6)?\.exe', "FSE_Processor_Setup_${NewVersion}_QT6.exe"
    $content | Set-Content $buildBatPath -NoNewline
    Write-Host "   OK build.bat aggiornato" -ForegroundColor $colorSuccess
    $updatedCount++
} else {
    Write-Host "   ATTENZIONE: build.bat non trovato" -ForegroundColor $colorWarning
}

# 4. version.json
Write-Host "4. Aggiornamento version.json..." -ForegroundColor $colorInfo
$versionJsonPath = Join-Path $PSScriptRoot "version.json"
if (Test-Path $versionJsonPath) {
    $versionData = Get-Content $versionJsonPath -Raw | ConvertFrom-Json
    $versionData.Version = $NewVersion
    $versionData.ReleaseDate = $ReleaseDate
    $versionData.ReleaseNotes = ""
    $versionData.Sha256Hash = ""
    $versionData.FileSize = 0
    $versionData | ConvertTo-Json -Depth 10 | Set-Content $versionJsonPath
    Write-Host "   OK version.json aggiornato (compilare ReleaseNotes e hash dopo il build)" -ForegroundColor $colorSuccess
    $updatedCount++
} else {
    Write-Host "   ATTENZIONE: version.json non trovato" -ForegroundColor $colorWarning
}

# 5. CHANGELOG.md (aggiunge sezione per la nuova versione se non esiste)
Write-Host "5. Aggiornamento CHANGELOG.md..." -ForegroundColor $colorInfo
$changelogPath = Join-Path $PSScriptRoot "CHANGELOG.md"
if (Test-Path $changelogPath) {
    $content = Get-Content $changelogPath -Raw
    $versionHeader = "## [$NewVersion] - $ReleaseDate"
    if ($content -match [regex]::Escape("## [$NewVersion]")) {
        Write-Host "   Sezione $NewVersion gia presente nel CHANGELOG" -ForegroundColor $colorWarning
    } else {
        # Inserisci dopo la prima riga "# Changelog"
        $insertPoint = "# Changelog"
        $newSection = "$insertPoint`n`n$versionHeader`n`n- TODO: aggiungere note di rilascio`n"
        $content = $content -replace [regex]::Escape($insertPoint), $newSection
        $content | Set-Content $changelogPath -NoNewline
        Write-Host "   OK CHANGELOG.md aggiornato (compilare note di rilascio)" -ForegroundColor $colorSuccess
        $updatedCount++
    }
} else {
    Write-Host "   ATTENZIONE: CHANGELOG.md non trovato" -ForegroundColor $colorWarning
}

# Riepilogo
Write-Host ""
if ($updatedCount -gt 0) {
    Write-Host "Versione aggiornata a $NewVersion in $updatedCount file!" -ForegroundColor $colorSuccess
} else {
    Write-Host "Nessun file aggiornato." -ForegroundColor $colorWarning
}
Write-Host ""
Write-Host "Prossimi passi:" -ForegroundColor $colorInfo
Write-Host "  1. Aggiorna CHANGELOG.md con le note di rilascio" -ForegroundColor $colorInfo
Write-Host "  2. Esegui build.bat per compilare" -ForegroundColor $colorInfo
Write-Host "  3. git add -A && git commit -m `"release: FSE Processor v$NewVersion`"" -ForegroundColor $colorInfo
Write-Host "  4. git tag -a v$NewVersion -m `"v$NewVersion`"" -ForegroundColor $colorInfo
Write-Host "  5. git push --all && git push --tags" -ForegroundColor $colorInfo
Write-Host ""
