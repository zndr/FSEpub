# Fix CDP Race Condition — v2.4.19 (2026-03-07)

**Commit:** `c9df97c` (fix) + `f91974e` (version bump)
**File:** `browser_automation.py`
**Status:** IN TEST

## Problema

Quando nessun browser è in esecuzione e l'utente avvia un download:
1. L'app lancia Edge con `--remote-debugging-port`
2. Si connette via CDP
3. **Non naviga** alla pagina FSE/SISS — l'utente non vede nessun tab SISS
4. L'app si blocca per sempre, anche aprendo manualmente un tab FSE

### Causa radice

Race condition in `_start_cdp()` (linee ~1474-1496):
- `_launch_browser_with_cdp()` avvia Edge
- Il polling loop chiama `_connect_cdp()` appena la porta CDP risponde (~1-2s)
- `_connect_cdp()` esegue `_cleanup_cdp_targets()` — ma Edge non ha ancora creato tutti i target interni (Copilot sidebar, extension pages, ecc.)
- `connect_over_cdp()` parte
- Edge finisce l'inizializzazione e crea target "other" **DOPO** che il cleanup è già stato eseguito
- Questi target tardivi causano **deadlock** in `connect_over_cdp`, che bypassa il suo timeout
- L'app è bloccata su `connect_over_cdp`, non raggiunge mai `wait_for_manual_login`

## Correzioni applicate

### Change 1: Attesa stabilizzazione browser (linee ~1474-1523)
- Separato il loop in due fasi: prima aspetta che la porta CDP sia attiva, poi attende **3 secondi** per permettere a Edge di creare tutti i target interni
- Aggiunto **retry loop** (2 tentativi) per cleanup+connect dopo il lancio
- Se il primo tentativo fallisce (deadlock rilevato), attende 2s e riprova con un nuovo cleanup

### Change 2: Hard timeout per `connect_over_cdp` (in `_connect_cdp`, linee ~1585-1615)
- `connect_over_cdp` ora gira in un **thread daemon** con timeout di **20 secondi**
- Se il thread è ancora vivo dopo 20s (deadlock), viene sollevato `ConnectionError` invece di bloccarsi per sempre
- Difesa in profondità: anche se il cleanup perde un target, l'app non si blocca

### Change 3: `bring_to_front()` dopo navigazione FSE (in `wait_for_manual_login`)
- Dopo `goto(FSE_BASE_URL)`, chiama `bring_to_front()` per mostrare il tab SSO all'utente

### Change 4: `domcontentloaded` al posto di `networkidle` (in `wait_for_manual_login`)
- Le pagine SSO possono avere attività di rete persistente che impedisce a `networkidle` di scattare
- `domcontentloaded` è sufficiente per verificare lo stato di autenticazione
- Aggiunto `timeout=30000` esplicito

## Checklist di test

- [ ] Browser chiuso → avvia download → Edge si apre E naviga a FSE/SSO login
- [ ] Completare login SSO → l'app rileva il login e procede col download
- [ ] Browser già aperto + autenticato → il flusso esistente funziona ancora
- [ ] Browser aperto senza CDP → messaggio di errore corretto (non hang)
- [ ] Tab FSE viene portato in primo piano (visibile all'utente)

## Rollback

Se il fix causa problemi, revertire il commit `c9df97c`:
```bash
git revert c9df97c
```
