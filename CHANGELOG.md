# Changelog

## [3.2.6] - 2026-09-10

### Aggiunto

- **Allegazione dei referti in Millewin, direttamente da FSE Processor (integrazione col progetto autoallega).** Dopo l'estrazione del testo e l'eventuale analisi A.I., FSE può ora proporre e guidare l'allegazione dei PDF agli accertamenti di Millewin: il nuovo menu Strumenti > "Allega referti a Millewin" elenca le proposte del proponitore di autoallega (paziente, accertamento, data di prescrizione, punteggio, stato), e — con l'opzione dedicata — l'elenco si apre da solo al termine di ogni download se ci sono referti allegabili (sia dal flusso email sia dal Download Paziente, in quel caso filtrato sul paziente). L'allegazione vera e propria passa per il canale già collaudato dell'app AutoAllega (attività pianificata elevata + coda su file, stesso lucchetto: i due client convivono); a operazione riuscita la tripletta pdf+txt+meta.json viene archiviata in `allegati_ok`. Prima di allegare, lo stato dell'accertamento viene ricontrollato sull'archivio (allegati già presenti, referto già scritto → conferma esplicita); i referti che richiedono un accertamento nuovo restano guidati ma manuali. Tutto opzionale e disattivato per default (Impostazioni > Processazione testo > "Allegazione referti in Millewin"); richiede il progetto autoallega installato. Nessuna scrittura sul database di Millewin: FSE legge soltanto e l'allegazione avviene nell'interfaccia di Millewin, sotto gli occhi dell'utente. Dettagli e checklist di collaudo in `docs/allegazione-millewin.md`.
- **Il nome dell'esame viene ora estratto insieme al referto: i referti smettono di finire tra i "non proponibili".** L'analisi A.I. riporta d'ora in poi anche il **nome dell'esame** (`Esame: RX Spalla`, accanto a data e medico in fondo al testo) e lo salva nel file di accompagnamento `.meta.json`. Serviva: senza, l'allegazione riusciva a riconoscere l'esame solo quando il radiologo l'aveva scritto per esteso come prima riga del referto — quando mancava, il referto veniva elencato fra quelli non proponibili anche se in Millewin la prescrizione corrispondente c'era. Il nome viene ripreso dall'intestazione del referto (che resta comunque esclusa dal testo, come prima) oppure, se l'intestazione tace, dalla descrizione dell'esame effettivamente eseguito; comprende sempre il distretto anatomico e la lateralità quando indicati ("RX Ginocchio destro"). Se il referto non permette di stabilirlo il campo resta **vuoto**: mai un nome inventato, perché a valle verrebbe confrontato con i nomi degli accertamenti prescritti. I referti già scaricati non vanno rifatti — per quelli l'allegazione continua a leggere il nome dal testo come prima.

### Corretto

- **Niente piu' notifiche di Windows a ogni referto analizzato (analisi A.I. "Claude CLI (locale)").** Con il provider "Claude CLI (locale)" ogni referto analizzato faceva comparire una notifica di Windows con suono - intestata col nome della cartella in cui e' installato il programma e col testo che iniziava per "Sei un assistente medico specializzato..." -: durante l'elaborazione di un gruppo di referti significava una notifica ogni pochi secondi. Il motivo: il comando `claude` non e' un semplice servizio a cui inviare il testo, e' una sessione completa di Claude Code, e come tale esegue anche le automazioni personali che l'utente ha configurato sulla propria postazione (fra cui i notificatori). Ora le sessioni avviate da FSE nascono con quelle automazioni disattivate; le sessioni che l'utente apre di persona restano intatte.

## [3.2.5] - 2026-08-09

### Corretto

- **CRITICO — I download di Edge non finiscono più "spariti" nella cartella temporanea con nomi illeggibili.** Collegandosi a Edge, il componente di automazione dirottava — a livello dell'intero browser e in modo permanente fino al riavvio di Edge — **tutti** i download verso una cartella temporanea nascosta, salvandoli con un nome in codice senza estensione (il "limbo"): potevano sparire così sia i referti sia i **download manuali dell'utente** in qualunque scheda di Edge, anche a FSE già chiuso (bastava ad esempio un "Esporta diagnostica" per innescarlo). Ora il dirottamento viene disattivato **immediatamente** a ogni collegamento, in ogni punto dell'app che si collega a Edge, e la disattivazione viene verificata con conferma del browser anche quando la pagina è già chiusa o bloccata.
- **Recupero automatico dei file già finiti nel "limbo".** A ogni avvio FSE ispeziona le cartelle temporanee lasciate dalle sessioni precedenti: i PDF ritrovati vengono spostati nella nuova sottocartella **`Recupero`** della cartella dei referti (nomi `recupero_DATA_ORA_N.pdf`, segnalati nel log); gli altri file — probabili download manuali dell'utente — vengono segnalati nel log con il percorso esatto, senza toccarli; le cartelle rimaste vuote vengono eliminate. I download eventualmente ancora in corso non vengono mai toccati.
- **Un salvataggio fallito non fa più perdere il referto.** Nella modalità di download di riserva, se il salvataggio del file falliva il referto restava abbandonato nella cartella temporanea e il paziente veniva marcato fallito: ora il salvataggio viene ritentato e, in extremis, il file viene recuperato direttamente dalla cartella temporanea; solo se ogni via fallisce si passa al fallback successivo.
- **Edge multi-profilo: la sessione SISS non finisce più nel profilo sbagliato.** Quando Edge era chiuso e FSE lo avviava, Edge apriva l'**ultimo profilo usato** — che su installazioni con più profili (es. lavoro + personale) poteva essere quello personale: la sessione SISS di FSE finiva così in un profilo con cookie separati da quello dove Edge apre i link di Millewin, con doppio login SISS e possibile disconnessione della sessione buona (il SISS ammette una sola sessione per operatore). Ora FSE avvia Edge indicando esplicitamente il profilo che usa abitualmente il portale operatori (riconosciuto dalla cronologia), che diventa anche l'"ultimo usato": i link di Millewin vengono instradati lì e la sessione resta condivisa.

## [3.2.4] - 2026-08-09

### Corretto

- **CRITICO — Eliminato lo scambio di persona dopo un "Errore nel servizio di consenso".** Quando un paziente veniva saltato per l'errore del servizio di consenso, il portale poteva restare fermo sulla sua scheda cittadino (pulsante 'Accedi' visibile); la navigazione verso il paziente successivo cambiava solo l'indirizzo della pagina senza farla ripartire, e FSE — trovando la pagina "già nel fascicolo" — entrava nel fascicolo del paziente **precedente** e ne scaricava il referto più recente **intestandolo al paziente successivo**. Ora l'identità del paziente è verificata in tre punti: il click su 'Accedi' viene evitato se la scheda mostra un codice fiscale diverso da quello atteso (evita anche l'accesso indebito al fascicolo altrui, tracciato dal portale); se la pagina risulta di un altro paziente viene ricaricata automaticamente per far ripartire la ricerca corretta; e prima di leggere la tabella dei referti (download da email, download completo, apertura da Millewin) il codice fiscale atteso **deve** comparire nella pagina, altrimenti il paziente viene marcato come fallito (riprovabile) e nulla viene mai scaricato o attribuito. Nel resoconto un eventuale blocco compare tra i "Download falliti" con il messaggio "La pagina FSE mostra un paziente diverso da quello atteso".

## [3.2.3] - 2026-07-24

### Corretto

- **Download più affidabile: la pagina del fascicolo bloccata non fa più fallire il paziente.** Tre problemi transitori del portale (SPA Angular) potevano marcare un referto come "fallito" anche se un nuovo tentativo manuale funzionava quasi sempre:
  - se dopo la navigazione la pagina del portale restava **vuota** (bootstrap bloccato), FSE ora la **ricarica da solo** — l'equivalente dell'F5 manuale che sbloccava la situazione — invece di attendere invano il form di ricerca;
  - il click su **'Referti'** poteva cadere nel vuoto quando il tab era visibile ma non ancora attivo: ora FSE verifica che la tabella dei referti compaia davvero e, in caso contrario, riprova;
  - il click su **'Accedi'** poteva bloccarsi per 60 secondi quando il portale entrava nel fascicolo da solo proprio mentre il click era in corso (pulsante rimosso dalla pagina a metà azione): ora il caso viene riconosciuto come successo e si prosegue subito.
- **Niente più schede duplicate del portale.** Quando una scheda del portale FSE era già aperta (ad es. accanto a una scheda SISS generica di Millewin), FSE ne apriva una nuova invece di riusarla: ora la scheda esistente viene sempre preferita.

## [3.2.0] - 2026-07-07

### Cambiato

- **Sessione SISS senza più opzioni: FSE lavora nella finestra di Edge di tutti i giorni.** Le tre strategie del gruppo "Sessione SISS" (avvio automatico, riavvio su conferma, browser dedicato) sono state eliminate: erano tutte soluzioni di ripiego al blocco del collegamento remoto introdotto dalle versioni recenti di Edge, che ora viene superato con la funzione ufficiale di Microsoft. Dopo un'**attivazione una-tantum** (in Edge: `edge://inspect` → *Remote debugging* → spunta su *Allow remote debugging for this browser instance* → riavvio di Edge; la spunta resta memorizzata), FSE si collega da solo alla **stessa sessione SISS di Millewin**: un solo login al giorno, Millewin mai scollegato, nessuna finestra in più, nessuna scrittura nel registro di Windows. Se l'attivazione manca, FSE si ferma con un messaggio che elenca i passaggi. Il passo "Sessione SISS" della Configurazione guidata è stato rimosso; la guida dedicata è stata riscritta. Chrome continua a usare il collegamento classico con riavvio su conferma.
- All'avvio FSE rimuove automaticamente i resti delle vecchie strategie (attività pianificata "FSE Processor - Sessione SISS", collegamento in Esecuzione automatica), oltre alle bonifiche già esistenti.

### Corretto

- **Referti "invisibili": riconosciuto il nuovo dominio delle notifiche di Regione Lombardia.** Da inizio luglio 2026 alcune email "Nuovo documento per..." contengono il link al fascicolo sull'host `dcss.cgi.crs.lombardia.it` invece dello storico `operatorisiss.servizirl.it`: FSE le scartava con "nessun link FSE trovato nel corpo" e i referti non venivano né contati né scaricati (sintomo: 6 non letti in posta, FSE ne trovava 2). Ora entrambi i domini sono riconosciuti (lista esplicita, per sicurezza) e la navigazione avviene sempre sul portale storico collaudato; un eventuale futuro cambio di dominio viene segnalato chiaramente nel log.
- **Il limite della versione non registrata non degrada più le impostazioni.** Se l'app si considerava non registrata anche una sola volta, il limite di 2 email per esecuzione veniva **scritto permanentemente** in "Max email da processare" e non veniva mai ripristinato dopo la registrazione. Ora il limite free si applica solo durante l'esecuzione, senza toccare l'impostazione dell'utente.
- **Referti estratti di nuovo leggibili.** Il testo estratto dai referti (specialistici, pronto soccorso, dimissioni ospedaliere) torna al comportamento della 2.5.24: righe correttamente a capo e solo i medici pertinenti. Un cambiamento introdotto nelle versioni 3.0.x aveva reso il testo di alcuni referti meno leggibile (testo continuo senza a capo, nominativi non pertinenti) e poteva far sì che l'analisi IA non evidenziasse i rilievi clinici.
- **Analisi IA con "Claude CLI (locale)" non più bloccata quando non è impostata una API key.** Usando il provider "Claude CLI (locale)" con il solo login all'abbonamento (senza inserire alcuna API key nelle impostazioni — la configurazione prevista per questa modalità), l'analisi veniva rifiutata con "API key mancante". Ora la modalità locale funziona con il solo login della CLI `claude`, come previsto. Chi vuole autenticarsi con una API key continua a usare il provider "Claude (Anthropic)".
- **Avviso esplicito quando l'analisi IA non è disponibile.** Se l'analisi IA fallisce in modo sistematico (login all'abbonamento assente o scaduto, credito API esaurito, chiave non valida, CLI `claude` non installata), l'app ora **avvisa l'utente** invece di salvare in silenzio il testo grezzo del referto — che, senza analisi, poteva sembrare un referto elaborato ma privo dell'interpretazione clinica.

### Note / limiti noti

- In alcuni referti specialistici con impaginazione a colonna laterale (es. Medicina Nucleare) l'intestazione/roster dello studio può ricomparire nel testo estratto. Non si tratta di dati personali del paziente (che restano correttamente mascherati). Una rimozione mirata è pianificata per una versione successiva (issue #48).

## [3.1.1] - 2026-07-04

### Corretto

- **Collegamento a Edge ripristinato dopo gli aggiornamenti recenti del browser.** Le versioni recenti di Microsoft Edge (basate su Chromium 136 e successive, es. Edge 150) impediscono per sicurezza il collegamento remoto quando il browser usa il profilo predefinito: dopo un aggiornamento di Edge, l'avvio della sessione SISS poteva restare bloccato con "Impossibile connettersi al browser". FSE ora riconosce automaticamente queste versioni di Edge e apre una propria finestra dedicata (profilo separato, sempre con collegamento remoto), che convive con l'Edge di tutti i giorni e con Millewin senza chiuderli. Al primo utilizzo va effettuato una volta il login al SISS nella finestra dedicata.
- La diagnostica del collegamento remoto ora distingue il blocco dovuto ai criteri aziendali (group policy `RemoteDebuggingAllowed` / `DeveloperToolsAvailability`, che richiede l'amministratore di sistema) dal blocco sul profilo predefinito, risolvibile automaticamente dall'app.

## [3.1.0] - 2026-06-29

### Aggiunto

- **Sessione SISS — collegamento remoto garantito (fine delle interruzioni "browser senza CDP").** Poiche' il collegamento remoto non puo' essere aggiunto a un browser gia' aperto, FSE ora offre tre strategie selezionabili nelle Impostazioni (gruppo *Sessione SISS*), ognuna con tooltip dedicato e spiegata nella guida utente:
  - **Avvio automatico all'accensione**: un'attivita' pianificata di Windows apre il browser con il collegamento remoto e il portale SISS a ogni accesso, prima di Millewin — cosi' la sessione e' sempre pronta e nessuna app deve riavviare il browser. L'attivazione richiede una conferma una-tantum (nessuna modifica al sistema senza consenso). Ideale con Millewin per non riavviare mai il browser.
  - **Chiudi e riavvia su conferma** *(predefinita)*: quando un download trova il browser senza collegamento remoto, FSE propone (su consenso esplicito) di chiuderlo e riaprirlo con il collegamento attivo. Funziona subito senza configurare nulla.
  - **Browser dedicato a FSE**: FSE usa una propria finestra di Edge con profilo separato, sempre con collegamento remoto, indipendente dal browser quotidiano e da Millewin.
- Nuovo pulsante **Avvia sessione SISS ora** e launcher dedicato (eseguibile anche come `--launch-siss`) per aprire all'istante il browser con il collegamento remoto + portale SISS. Nessuna modifica al registro di Windows: il flag e' garantito solo lanciando il browser con l'argomento esplicito.

### Corretto

- Provider "Claude CLI (locale)": la verifica della connessione non fallisce piu' con "Connessione a Claude CLI (locale) fallita. Verifica API key e connessione internet." quando e' presente un abbonamento Claude valido. La modalita' locale ora usa sempre il login locale della CLI `claude`: la API key salvata nelle impostazioni non viene piu' iniettata in `ANTHROPIC_API_KEY` (la CLI le dava la precedenza sul login, e una key con credito esaurito o revocata faceva fallire la connessione anche con un abbonamento funzionante). Per autenticarsi con una API key usare il provider "Claude (Anthropic)".

## [3.0.0] - 2026-06-11

Versione maggiore: include la revisione completa di sicurezza, privacy e stabilita' (46 correzioni in 5 ondate) e il nuovo modello CDP "process-local" che elimina ogni modifica al registro di Windows.

### Sicurezza e privacy

- Anonimizzazione piu' robusta prima dell'invio ai servizi di analisi: nomi composti con particelle (DE, LA, DEL...), campo Sesso, comune di nascita e numeri di telefono vengono riconosciuti correttamente; nei log dell'app il codice fiscale e il nome del paziente sono sempre mascherati, anche nei nomi file.
- Le password salvate usano ora una chiave di cifratura legata alla singola installazione (le credenziali esistenti continuano a funzionare).
- La verifica della licenza non puo' piu' essere aggirata da risposte memorizzate nella cache.
- Rimossa ogni modifica automatica del registro di Windows per CDP: Edge/Chrome vengono avviati o riavviati con il flag `--remote-debugging-port` solo dall'app, su una porta dedicata alla sessione. All'avvio l'app ripulisce gli override legacy lasciati nel registro da versioni precedenti, che potevano impedire l'apertura del browser da icona, rompere i link esterni e bloccare i download manuali e l'interazione SISS.
- All'avvio il comportamento nativo dei download del browser viene ripristinato ("default") tramite CDP, senza intercettare i download dell'utente o di Millewin.

### Integrita' dei referti

- Un download che non restituisce un PDF valido viene ora segnalato come errore invece di essere salvato come referto riuscito.
- La rinomina dei file e' atomica: due referti non possono piu' sovrascriversi a vicenda, nemmeno con nomi identici.
- Il filtro mittente delle notifiche email funziona anche con intestazioni spezzate su piu' righe; il registro delle email gia' elaborate non puo' piu' corrompersi se due istanze scrivono insieme.
- Prima di riprovare un download fallito l'app ri-verifica di essere sul fascicolo del paziente giusto.

### Stabilita'

- Accesso con Firma Remota piu' rapido: il pulsante viene cliccato appena compare, senza l'attesa fissa di 10 secondi.
- Interfaccia piu' reattiva durante le operazioni lunghe: il visualizzatore dell'archivio patologico e i controlli di stato non bloccano piu' la finestra.
- Corretti errori che potevano lasciare la procedura guidata o i timer di aggiornamento in uno stato incoerente alla chiusura.

### Modificato

- Rimosse le caselle "Abilita CDP nel registro" dal wizard e dalle Impostazioni: con il CDP process-local non hanno piu' effetto.

## [2.5.24] - 2026-06-03

### Migliorato

- Ricerca paziente piu' reattiva: se il fascicolo e' gia' aperto, l'app riusa la pagina esistente invece di ricaricarla a ogni filtro o cambio paziente.
- All'avvio l'app riusa la scheda del portale gia' aperta (OpeFseIE) invece di aprirne una nuova ogni volta.
- Guida utente aggiornata con nuove sezioni e schermate.

### Corretto

- L'"Errore nel servizio di consenso" e' ora gestito come accesso da autorizzare: il referto viene saltato e registrato, senza interrompere l'elaborazione degli altri.
- Corretta la voce di menu Aiuto che non apriva la Guida.

### Modificato

- Nella versione gratuita il limite di referti elaborabili passa da 5 a 2 (esteso anche al download da ricerca paziente).

## [2.5.23] - 2026-06-01

### Corretto

- Risolto un problema per cui, dopo un aggiornamento, l'app poteva non scaricare piu' i referti: la connessione al browser falliva con l'errore "Connection closed while reading from the driver". La causa era un file interno di Playwright rimasto da una versione precedente che mandava in crash il componente di automazione. L'installer ora ripulisce sempre i componenti interni prima di copiare quelli nuovi, evitando residui incompatibili tra versioni (upgrade e downgrade).
- Corretto un falso avviso "Playwright driver non trovato" al termine della compilazione (lo script di build cercava il driver nel percorso sbagliato per i bundle PyInstaller 6.x).

## [2.5.22] - 2026-06-01

### Migliorato

- L'anonimizzazione dei referti e' meno aggressiva e ora conserva i dati clinicamente rilevanti che non identificano il paziente: le date del referto (esecuzione, prelievo, refertazione, ricovero e dimissione) e il nome del medico firmatario in calce non vengono piu' rimossi.
- La data di nascita del paziente viene riconosciuta in modo mirato a partire dal codice fiscale e oscurata ovunque compaia, mantenendo intatte tutte le altre date utili alla lettura del referto.

### Corretto

- Il nome del medico firmatario non viene piu' oscurato quando e' scritto tutto in maiuscolo con il titolo nel mezzo (es. "BARTONE DOTT.SSA LAURA").
- Migliorata l'affidabilita' della verifica TLS nei pacchetti installati: il bundle dell'installer include ora i certificati di certifi (cacert.pem).

## [2.5.21] - 2026-05-31

### Sicurezza

- Le connessioni a GitHub per il controllo aggiornamenti e la verifica delle licenze ora validano sempre il certificato TLS, proteggendo da intercettazioni in rete.
- L'oscuramento dei dati personali nelle informazioni di debug inviate al supporto e' piu' ampio: copre codici fiscali (anche nelle varianti omocodiche), indirizzi email, numeri di telefono, tessera sanitaria e date. L'invio richiede una conferma esplicita quando sono presenti allegati.
- La password recuperata non viene piu' copiata automaticamente negli appunti al momento della visualizzazione.

### Migliorato

- L'avviso "SISS non raggiungibile" compare ora una sola volta finche' la connessione non viene ripristinata, invece di ripetersi ad ogni controllo periodico.
- Etichette dell'interfaccia uniformate in italiano (Informazioni, Azzera console, Informazioni di debug e altre).

### Corretto

- Risolto un possibile arresto anomalo della diagnostica del browser (modalita' CDP).
- Risolto un possibile arresto anomalo all'avvio in presenza dell'integrazione Millewin.
- Corretta la visualizzazione del simbolo "meno" nell'elenco delle terapie rimosse.

## [2.5.20] - 2026-05-31

### Aggiunto

- Report dei reperti patologici per **giorno di download**: un riepilogo dei referti scaricati in una giornata, con i reperti evidenziati per gravita' (+, ++, +++) ed eventuale commento clinico. Il giorno si sceglie liberamente da un calendario oppure dall'elenco dei giorni in cui risultano download.
- Pulsante **"Report patologici"** e opzione **"Visualizza report dei reperti patologici"** al termine del download, nella scheda Referti SISS. Sono disponibili quando e' attiva la modalita' "Estrazione e analisi con A.I.".
- **Archivio cumulativo** dei reperti patologici in un database locale (opzionale), con un visualizzatore interno che permette di filtrare per data, gravita' e nome del paziente.
- **Esportazione in Excel** dell'archivio dei reperti patologici (opzionale), filtrabile per intervallo di date.

### Migliorato

- Il report del giorno include tutti i referti scaricati in quella giornata, riconoscendoli anche dalla data del file: cosi' non viene tralasciato nulla anche se l'elenco interno dei download e' incompleto.
- La marcatura "Segna come visionati" richiede ora una conferma esplicita e agisce solo sul giorno effettivamente generato; il report distingue chiaramente l'assenza di referti da un eventuale errore di generazione.

## [2.5.19] - 2026-05-29

### Migliorato

- Terminazione e riavvio del browser piu' robusti: viene chiuso l'intero albero dei processi Edge/Chrome (incluse le finestre figlie), riducendo i casi in cui restano processi "orfani" che impediscono al browser di riaprirsi correttamente.
- Se la chiusura del browser non riesce, l'app non avvia piu' una seconda istanza sopra quella bloccata, evitando i conflitti che potevano impedire l'apertura di Edge dall'icona.
- Chiusura piu' pulita dell'applicazione: il browser viene terminato in modo ordinato anche all'uscita.
- Migliorata la diagnostica nei log per l'analisi dei processi del browser.

### Corretto

- Ripristinata la chiusura forzata di emergenza del browser come ultima risorsa quando la terminazione ordinata viene negata dal sistema (processi protetti o elevati).

## [2.5.18] - 2026-05-24

### Corretto

- Estrazione del codice fiscale dalla finestra Millewin non funzionante in alcuni scenari.

## [2.5.17] - 2026-05-22

### Aggiunto

- Tab "Diff terapia": confronto di terapie, problemi ed esami del paziente prima e dopo un ricovero, con export PDF.
- Generatore di licenze: scheda Impostazioni per l'account email mittente e pulsante "Copia chiave".

### Corretto

- Crash all'avvio su sistemi con Millewin appena installati (variabile di log non definita nella gestione di un errore previsto).
- Riconoscimento della sessione SISS gia' attiva (tab aperta da Millewin): l'app la eredita senza piu' richiedere un login manuale superfluo.
- Connessione CDP con Microsoft Edge v148 e successivi (riconoscimento del prefisso "Edg/").
- Chiusura delle schede del browser piu' rispettosa: le schede pre-esistenti di Millewin o dell'utente non vengono piu' chiuse.
- Conteggio delle email nella barra di stato: lettura corretta delle cartelle IMAP configurate.
- Referti gia' scaricati non vengono piu' ri-scaricati: le email vengono ora correttamente contrassegnate come lette.
- Nome e cognome non piu' invertiti nel report patologico aggregato.

### Migliorato

- Al termine dei download la sessione SISS resta sempre attiva nel browser (rimossa la richiesta di conferma).
- Maggiore stabilita' della connessione CDP, dello scoping dei download e della validazione di sessione.

## [2.5.16] - 2026-03-30

### Corretto

- Fix download PDF in modalita' CDP con Chrome 146: sostituito `time.sleep()` con `page.wait_for_timeout()` nel polling loop — `time.sleep()` bloccava l'event loop di Playwright impedendo la consegna degli eventi download
- I download manuali da altri siti durante il processamento vengono ora salvati automaticamente nella cartella Downloads dell'utente

### Aggiunto

- Handler globale per i download manuali: i file scaricati dall'utente durante il processamento CDP vengono salvati in ~/Downloads
- Warning popup al primo avvio del processamento CDP con opzione "Non mostrare piu'"
- Menu Strumenti > "Apri cartella Downloads utente" per accedere ai download manuali
- Nota nella guida utente sul comportamento download in modalita' CDP
- Salvataggio licenza silenzioso con aggiornamento titolo e status bar

## [2.5.15] - 2026-03-29

### Corretto

- Fix download in modalita' CDP: Ctrl+J e pannello download di Edge ora funzionano correttamente durante l'uso dell'app. L'intercettazione download di Playwright viene attivata solo durante lo scaricamento automatico dei PDF e ripristinata immediatamente dopo.

## [2.5.13] - 2026-03-27

### Corretto

- Connessione CDP robusta: polling salute post-cleanup al posto di sleep fisso
- Fix propagazione cleanup_done tra Step 0 e Step 3 che impediva cleanup fresco sui retry
- Verifica salute porta CDP tra i tentativi di connessione con backoff adattivo
- Tentativo finale senza cleanup come ultima risorsa prima di richiedere riavvio browser
- Riavvio automatico istanza Playwright dopo errori persistenti del driver


## [2.5.11] - 2026-03-26

### Corretto

- Analisi AI con Claude CLI: aggiunto flag --model e sostituito pipe shell fragile
- Integrazione DB Millewin per anonimizzazione basata su dizionario paziente
- Scrubbing PII inline potenziato con fallback basato su nome file
- Impostazione PRIAMO_ENABLED con dialogo riavvio CDP rinviato a on-demand

## [2.5.10] - 2026-03-25

### Aggiunto

- Integrazione database Millewin per anonimizzazione basata su dizionario paziente
- Impostazione PRIAMO_ENABLED con dialogo riavvio CDP rinviato a on-demand

### Migliorato

- Anonimizzazione potenziata con scrubbing PII inline e fallback basato su nome file

## [2.5.9] - 2026-03-24

### Aggiunto

- Report patologici aggregato: nuova funzionalita' che scansiona tutti i file .txt estratti e genera un report riepilogativo con reperti patologici ordinati per gravita' (+++ > ++ > +), raggruppati per paziente e data, con sezione "Reperti normali" in fondo
- Accessibile da menu Strumenti > Report patologici e da pulsante nella barra inferiore
- Elaborazione interamente locale (nessun dato inviato a server esterni)
- Report esportabile in formato testo o copiabile negli appunti


## [2.5.7] - 2026-03-09

- TODO: aggiungere note di rilascio


## [2.4.11] - 2026-03-06

- TODO: aggiungere note di rilascio


## [2.4.10] - 2026-03-05

- TODO: aggiungere note di rilascio


## [2.4.7] - 2026-03-05

### Corretto

- Conteggio email non letti nella status bar: ora conta solo le notifiche FSE con fetch headers e filtro client-side (FROM/SUBJECT), allineato alla stessa logica di "Controlla Email" per eliminare mismatch tra status bar e pulsante
- Rilevamento sessione SISS via CDP: corretto errore "405 Method Not Allowed" con Edge/Chromium 120+ (metodo PUT per /json/new, fallback GET per browser vecchi)
- Timing probe SISS: sostituito sleep fisso di 4s con polling adattivo (0.5s intervalli, max 10s) che esce appena l'URL si risolve — piu' veloce (~3s) e piu' affidabile su connessioni lente

## [2.4.6] - 2026-03-05

### Corretto

- Conteggio email non letti nella status bar: ora conta solo le notifiche FSE (filtro FROM/SUBJECT server-side) e sottrae i referti gia' scaricati tramite tracking locale, evitando conteggi gonfiati che inducevano a download duplicati
- Rilevamento sessione SISS via CDP: corretto errore "405 Method Not Allowed" con Edge/Chromium 120+ che richiede metodo PUT per l'endpoint /json/new (fallback GET per browser piu' vecchi)

## [2.4.0] - 2026-03-02

### Aggiunto

- Nuovo dialog "Visiona testi" nel menu Strumenti: confronto side-by-side tra testo originale estratto dal PDF e testo anonimizzato
- Evidenziazione visiva delle righe redatte (sfondo rosa, testo rosso) con tooltip che mostra il motivo della redazione
- Ri-estrazione on-demand dal PDF senza modifiche al pipeline esistente
- Doppio-click su un referto per confronto rapido

## [2.3.15] - 2026-02-26

### Aggiunto

- Guida utente: nuova sezione 6 "Estrazione testo e analisi referti con A.I." con documentazione completa delle funzionalita di text extraction, anonimizzazione, provider AI supportati (Claude, ChatGPT, Gemini, Mistral, Claude CLI, endpoint custom), configurazione API key, costi di utilizzo, analisi manuale e batch
- Guida utente: nuova voce troubleshooting per errori API key e analisi A.I.

## [2.3.14] - 2026-02-26

### Corretto

- Sessione SISS preservata in modalita CDP: il sessionStorage viene clonato dai tab esistenti e iniettato nel tab di automazione via init script, evitando nuovi login SSO che invalidavano l'accesso di Millewin
- Download manager del browser ripristinato dopo la disconnessione CDP: reset di Browser.setDownloadBehavior prima del distacco, risolvendo download manuali incompleti e Ctrl+J non funzionante
- Tab esistenti (about:blank, newtab) vengono riutilizzati invece di crearne di nuovi
- Guida utente aggiornata alla v2.3.14: documentazione wizard configurazione, segnalazione problemi con allegati, troubleshooting download e sessioni CDP

## [2.3.12] - 2026-02-23

### Aggiunto

- Allegati immagine nel dialog Debug: pulsanti "Aggiungi immagine..." (file picker) e "Incolla da clipboard" per allegare screenshot alle segnalazioni
- Le immagini vengono inviate come allegati MIME nell'email di debug

## [2.3.11] - 2026-02-23

### Aggiunto

- Wizard di configurazione guidata (7 step): si avvia automaticamente alla prima installazione quando settings.env non esiste o EMAIL_USER e' vuoto
- Accessibile anche da menu Aiuto > Configurazione guidata per riconfigurare l'app
- Step: Benvenuto, Account Email, Server IMAP (con test connessione e sfoglia cartelle), Cartelle, Browser e PDF, Parametri, Riepilogo
- Nota informativa POP3 nello step IMAP per utenti con client di posta POP3
- Tooltips esplicativi su tutte le opzioni del wizard (CDP, headless, timeout, etc.)
- Opzioni "Abilita CDP nel registro" e "Headless browser" nello step Browser
- Step Parametri per timeout download/pagina e dimensione carattere console

## [2.3.10] - 2026-02-23

### Corretto

- Nascosta la finestra console dei sottoprocessi node.exe di Playwright: monkey-patch di subprocess.Popen con flag CREATE_NO_WINDOW in modalita frozen, per evitare console visibili su alcune configurazioni Windows

## [2.3.9] - 2026-02-23

### Aggiunto

- Supporto multi-cartella IMAP: il campo "Cartelle IMAP" accetta piu cartelle separate da virgola
- Pulsante "Sfoglia..." per selezionare le cartelle IMAP dal server tramite dialog con caselle di spunta
- Riconnessione automatica IMAP (NOOP check) prima di mark_as_read/delete per gestire timeout durante login SSO
- Guida utente aggiornata alla versione corrente con documentazione cartelle IMAP e folder picker

### Corretto

- Messaggi marcati manualmente come non letti venivano ignorati: il filtro locale processed_uids ora si applica solo nel fallback ALL, non sui messaggi UNSEEN
- UID tracking ora usa chiavi composite folder:uid per evitare collisioni tra cartelle diverse

## [2.3.7] - 2026-02-23

### Corretto

- Messaggi UNSEEN ignorati quando gia tracciati localmente: corretto il filtro processed_uids

## [2.3.6] - 2026-02-23

### Corretto

- Controllo aggiornamenti mostrava versioni piu vecchie come aggiornamenti disponibili: ora usa confronto semantico (tuple) invece di semplice disuguaglianza di stringhe

## [2.3.5] - 2026-02-23

### Aggiunto

- Nuovo riquadro "Download e processazione dei referti" nelle Impostazioni con campi per spostamento referti, estrazione testo e relative directory
- Tema chiaro forzato (Fusion + QPalette) per garantire leggibilita su Windows con tema scuro
- Modalita CDP e abilitazione CDP nel registro attive di default

### Modificato

- Timeout download aumentato da 60 a 120 secondi
- Timeout pagina aumentato da 30 a 60 secondi

## [2.3.4] - 2026-02-23

### Corretto

- Email non marcate come lette dopo il download: aggiunto auto-reconnect IMAP prima di impostare il flag `\Seen` (la connessione poteva scadere durante il login SSO manuale)
- Usato `BODY.PEEK[]` al posto di `RFC822` nel fetch per non impostare `\Seen` prematuramente
- Testo console invisibile su Windows con tema scuro: aggiunto colore testo esplicito a tutti i widget per sovrascrivere la palette di sistema

## [2.3.3] - 2026-02-23

### Corretto

- Fix errore certificato SSL nel controllo aggiornamenti: skip verifica SSL per la richiesta al URL GitHub hardcoded (nessun dato sensibile coinvolto)

## [2.3.2] - 2026-02-23

### Corretto

- Fix errore certificato SSL su sistemi nuovi: uso esplicito del Windows Certificate Store invece di cacert.pem che puo mancare nei bundle PyInstaller frozen

## [2.3.1] - 2026-02-22

### Corretto

- Fix installazione Chromium su sistemi nuovi: aggiunto flag `--with-deps` e cattura stderr per migliore segnalazione errori

## [2.3.0] - 2026-02-22

### Aggiunto

- Auto-polling Millewin: checkbox per monitoraggio automatico cambio paziente
- Rilevamento installazione: tab Millewin nascosto se Millewin non e installato

### Corretto

- Fix rilevamento servizio pgmille su Windows con localizzazione non inglese

## [2.2.1] - 2026-02-22

### Aggiunto

- Controllo automatico aggiornamenti all'avvio: l'app verifica silenziosamente la disponibilita di nuove versioni 2 secondi dopo l'apertura della finestra
- Se disponibile un aggiornamento, l'utente viene avvisato con dialog e possibilita di scaricare direttamente
- Nessuna interruzione se la versione e gia aggiornata o se la rete non e disponibile

### Modificato

- Refactoring metodo `_check_updates()` con parametro `silent` per distinguere il controllo automatico (silenzioso) da quello manuale (menu Aiuto)

## [2.2.0] - 2026-02-22

### Aggiunto

- Crittografia credenziali email: la password viene salvata crittografata in `settings.env` con prefisso `ENC:` (Fernet + PBKDF2HMAC legato all'identita Windows)
- Dialog dedicato "Cambia password" con verifica della password attuale, nuova password e conferma
- Migrazione automatica: le password in chiaro vengono crittografate al primo avvio senza intervento dell'utente
- Nuovo modulo `credential_manager.py` per gestione centralizzata delle credenziali
- Dipendenza `cryptography>=42.0.0`

### Modificato

- Campo password email reso read-only con pulsante "Cambia..." a fianco
- `config.py` decritta automaticamente `EMAIL_PASS` al caricamento

## [2.1.0] - 2026-02-22

### Aggiunto

- Tema blu professionale con barra dei menu
- Pulsante "Carica strutture" per pre-caricare il dropdown Ente prima del download
- Pulsante "Interrompi" per fermare il ciclo di attesa login
- Dialogo di conferma al salvataggio delle impostazioni
- Suffisso _QT6 al nome file installer

### Modificato

- Checkbox sotto-tipo referto compattate in riga singola con label brevi e tooltip
- Tab paziente migliorato: placeholder Ente, feedback progresso, riepilogo e pulsanti di pulizia
- Migliorata visibilita e gestione finestra browser durante l'automazione
- Messaggio di fallback migliorato quando il browser di sistema non e disponibile

### Corretto

- Fix connessione CDP: validazione endpoint con HTTP, timeout aumentato e retry su sessione stale

## [2.0.0] - 2026-02-21

### Modificato

- Migrazione completa interfaccia grafica da Tkinter a PySide6 (Qt6)
- Look nativo Windows moderno con widget Qt6
- Tooltip nativi Qt al posto della classe Tooltip custom
- Sistema threading riscritto con Qt Signal/Slot e QTimer al posto di `.after()`
- Dialoghi nativi Qt (QMessageBox, QFileDialog, QDialog)
- Layout con QVBoxLayout/QHBoxLayout/QGridLayout al posto di pack/grid Tkinter

### Rimosso

- Dipendenza da Tkinter
- Classe Tooltip custom (sostituita da setToolTip nativo)
- StringVar/BooleanVar (sostituiti da accesso diretto ai widget)

### Aggiunto

- Dipendenza PySide6 >= 6.6.0
- Classe _SignalBridge per comunicazione thread-safe tra worker e GUI

## [1.1.0] - 2026-02-21

### Aggiunto

- Guida utente HTML standalone (`guida_utente.html`) con navigazione, sezioni collassabili e layout responsive
- Pulsante "Guida" nel Tab SISS per aprire la guida nel browser
- Tab Paziente: download referti per singolo paziente tramite codice fiscale
- Tab Paziente: filtri per ente/struttura e periodo (settimana, mese, anno, personalizzato)
- Tab Paziente: selezione gerarchica tipologie documento (Lab, Imaging, Anat. Pat., Specialistica, Dimissione, PS)
- Pulsante "Test connessione" nelle impostazioni Server Posta
- Tooltip descrittivi su tutti i controlli tecnici
- Sezione troubleshooting per conflitto porta CDP nella guida utente
- Guida inclusa nel bundle PyInstaller e nell'installer

### Modificato

- Porta CDP rimossa dalla UI (resta configurabile in `settings.env` per utenti avanzati)
- Layout impostazioni riorganizzato in due colonne

## [1.0.0] - 2026-02-21

Prima release di FSE Processor.

### Funzionalita

- Connessione POP3 per recupero email con referti FSE
- Automazione browser (Playwright) per download documenti dal portale FSE
- Rinomina automatica PDF con nome paziente e codice fiscale
- GUI Tkinter con interfaccia a schede (Integrazione SISS / Impostazioni)
- Rilevamento automatico browser installati (Edge, Chrome, Firefox, Brave)
- Rilevamento automatico lettori PDF dal registro di Windows
- Supporto Chrome DevTools Protocol (CDP) per sessione browser esistente
- Modalita installata (Program Files + AppData) e portatile
- Installer Inno Setup con supporto italiano/inglese
- Logging strutturato con statistiche di sessione
- Tracciamento UID email processate per evitare duplicati

---

Tutte le modifiche importanti a questo progetto saranno documentate in questo file.

Il formato e basato su [Keep a Changelog](https://keepachangelog.com/it/1.0.0/),
e questo progetto aderisce al [Semantic Versioning](https://semver.org/lang/it/).
