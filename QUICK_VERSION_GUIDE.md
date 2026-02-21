# FSE Processor - Guida Rapida Versioning

## Aggiornare la Versione

```powershell
cd D:\Claude\FSE
.\Update-Version.ps1 -NewVersion "1.1.0"
```

## Schema Versioning

```
MAJOR.MINOR.PATCH
  1  .  0  .  0

MAJOR  -> Breaking changes, riscritture importanti
MINOR  -> Nuove funzionalita backward-compatible
PATCH  -> Bug fix, correzioni minori
```

## File Aggiornati Automaticamente

| File | Contenuto aggiornato |
|------|---------------------|
| `version.py` | `__version__ = "X.Y.Z"` (source of truth) |
| `installer.iss` | `#define MyAppVersion "X.Y.Z"` |
| `build.bat` | Nome file output installer |
| `version.json` | Metadati release |
| `CHANGELOG.md` | Nuova sezione versione |

## Esempi Rapidi

### Bug Fix (PATCH)
```powershell
.\Update-Version.ps1 -NewVersion "1.0.1"
# Aggiorna CHANGELOG.md con le note
git add -A && git commit -m "fix: descrizione del fix"
git tag -a v1.0.1 -m "Bug fix"
git push --all && git push --tags
```

### Nuova Feature (MINOR)
```powershell
.\Update-Version.ps1 -NewVersion "1.1.0"
# Aggiorna CHANGELOG.md con le note
git add -A && git commit -m "feat: descrizione feature"
git tag -a v1.1.0 -m "Nuova feature"
git push --all && git push --tags
```

### Breaking Change (MAJOR)
```powershell
.\Update-Version.ps1 -NewVersion "2.0.0"
# Aggiorna CHANGELOG.md con le note
git add -A && git commit -m "feat!: descrizione breaking change"
git tag -a v2.0.0 -m "Major release"
git push --all && git push --tags
```

## Verifica Versione

```powershell
# Verifica version.py
python -c "from version import __version__; print(__version__)"

# Verifica tutti i riferimenti
Select-String -Pattern '1\.\d+\.\d+' -Path version.py, installer.iss, version.json
```

## Workflow Completo di Release

1. `.\Update-Version.ps1 -NewVersion "X.Y.Z"`
2. Aggiornare CHANGELOG.md con le note di rilascio
3. `build.bat` per compilare exe + installer
4. `git add -A && git commit -m "release: FSE Processor vX.Y.Z"`
5. `git tag -a vX.Y.Z -m "vX.Y.Z"`
6. `git push --all && git push --tags`

## Troubleshooting

### Script PowerShell non si avvia
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Update-Version.ps1 -NewVersion "1.0.0"
```
