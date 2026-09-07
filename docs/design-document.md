# Progetto Portafoglio Azionario — Documento di Progettazione

Ultimo aggiornamento: 4 luglio 2026

## 1. Visione d'insieme

Il progetto collega tre obiettivi in un'unica pipeline:

Screening (trovo candidati) → Backtesting (valido le regole su quei candidati e sulle logiche di gestione) → Monitoraggio (seguo nel tempo il portafoglio reale/simulato costruito con quelle regole).

I tre moduli condividono la stessa base dati (prezzi, fondamentali, macro, news) e le stesse funzioni di calcolo (rendimenti, rischio, metriche di performance), quindi conviene costruirli come librerie riutilizzabili piuttosto che tre progetti separati.

Stack scelto: Python (pandas, notebook Jupyter per l'esplorazione, script per l'automazione).

Il progetto si esegue per fasi successive (sezione 2): non ha senso iniziare lo screening se la base dati sotto non è solida, quindi la prima fase da completare è la costruzione delle fondamenta e della pipeline dati condivisa (sezione 9).

## 2. Fasi di esecuzione del progetto

Ogni fase produce qualcosa di utilizzabile prima di passare alla successiva. Non si passa alla fase N+1 finché la fase N non soddisfa i suoi criteri di completamento.

**Fase 1 — Fondamenta e Data Pipeline condivisa.** Ambiente di lavoro, struttura del repo, gestione configurazione e segreti, moduli di ingestion per ciascuna fonte dati, pulizia/validazione, schema dati comune, feature di base (rendimenti, volatilità), storage, automazione minima e test di qualità dati. È la fase più lunga e più importante: tutto il resto si appoggia su di essa. Dettaglio completo in sezione 9.

**Fase 2 — Screening.** Costruzione degli indicatori fondamentali/tecnici, dei filtri e dello score sull'universo titoli, usando esclusivamente il layer dati prodotto in Fase 1. Output: shortlist motivata di candidati.

**Fase 3 — Backtesting.** Motore di backtest (scelta libreria o motore custom), applicazione delle regole di entrata/uscita sui candidati, gestione rigorosa di look-ahead bias e survivorship bias, confronto con benchmark.

**Fase 4 — Monitoraggio.** Tracciamento del portafoglio reale/simulato nel tempo, calcolo di performance e rischio per posizione e aggregati, report periodici.

**Fase 5 — Automazione ed estensioni.** Scheduling stabile degli aggiornamenti, dashboard/report condivisibili, eventuale estensione a nuovi mercati, nuove fonti dati o nuove asset class.

Le sezioni 3-8 di questo documento restano la descrizione trasversale di moduli, fonti, strumenti, architettura e trasformazioni valida per tutte le fasi. La sezione 9 è il piano operativo dettagliato della Fase 1.

## 3. I tre moduli: cosa fanno

### 3.1 Screening — selezione titoli

Punto di partenza: un universo di azioni (es. S&P 500, FTSE MIB, o un paniere custom). Per ciascun titolo si calcolano indicatori fondamentali e tecnici, si applicano filtri/soglie, si assegna uno score, si produce una shortlist di candidati con motivazione.

Output: lista di titoli candidati + punteggio + motivazione (es. "P/E basso rispetto al settore", "crescita utili > 15% ultimi 3 anni", "momentum positivo a 6 mesi").

### 3.2 Backtesting — validazione delle regole

Punto di partenza: una strategia definita in modo esplicito (regole di entrata/uscita, criteri di ribilanciamento, pesi per posizione, eventuale stop loss). Si applica la strategia su dati storici e si confrontano i risultati con un benchmark (es. indice di mercato).

Output: curva di equity simulata, metriche di rischio/rendimento, confronto con benchmark, analisi dei periodi di maggior perdita.

Attenzione particolare a: look-ahead bias (usare solo dati disponibili a quella data), survivorship bias (l'universo storico deve includere anche titoli poi delistati/falliti), costi di transazione e slippage, capitale minimo per diversificazione.

### 3.3 Monitoraggio — portafoglio nel tempo

Punto di partenza: la composizione reale (o simulata) del portafoglio, con date e prezzi di carico delle posizioni. Si aggiorna periodicamente con i prezzi correnti, si calcola la performance complessiva e per singola posizione, si confronta con un benchmark, si producono report periodici.

Output: dashboard/report con valore del portafoglio nel tempo, contributo di ogni posizione, allocazione per settore/area geografica, indicatori di rischio aggiornati.

## 4. Fonti dati e tipi di dati

### 4.1 Prezzi storici e intraday (OHLCV)

Dato: Open, High, Low, Close, Volume, per azione, a frequenza giornaliera (minima necessaria) o intraday.

Fonti principali (verificate 2026):

- yfinance (libreria Python non ufficiale su dati Yahoo Finance): gratuita, nessun limite ufficiale di rate, ma è uno scraping non ufficiale — può rompersi senza preavviso e non ha garanzie di SLA. Ottima per prototipare.
- Alpha Vantage: API ufficiale gratuita, ma tier free molto limitato (25 richieste/giorno, 5 al minuto). Utile per dati fondamentali o serie che non richiedono refresh frequenti.
- Finnhub: tier gratuito con 60 richieste/minuto, quotazioni real-time con 20 minuti di ritardo sul piano free. Buona alternativa/backup a yfinance.
- Twelve Data: tier gratuito 800 chiamate/giorno, dati con 4 ore di ritardo.

Per il progetto: yfinance come fonte primaria (comoda per serie storiche lunghe), con Alpha Vantage o Finnhub come fallback/validazione incrociata.

### 4.2 Dati fondamentali

Dato: bilanci (stato patrimoniale, conto economico, flussi di cassa), utili per azione (EPS), ricavi, margini, debito, multipli di valutazione (P/E, P/B, EV/EBITDA), dividendi e dividend yield, calendario delle trimestrali.

Fonti: Alpha Vantage (endpoint fondamentali inclusi nel free tier con i limiti sopra), Finnhub (fondamentali base nel free tier), yfinance (espone alcuni dati fondamentali di Yahoo Finance, con la stessa incertezza di affidabilità del resto della libreria).

### 4.3 News e sentiment

Dato: notizie finanziarie per titolo/settore, punteggio di sentiment (positivo/neutro/negativo), volumi di menzione.

Fonti: NewsAPI (tier gratuito 100 richieste/giorno, buono per prototipare, va centellinato), Finnhub (ha un endpoint news per company), Reddit via PRAW (gratuito, utile per sentiment retail su subreddit tipo r/investing, r/wallstreetbets), Google Trends via pytrends (gratuito, non ufficiale, utile come proxy di attenzione/interesse pubblico su un titolo o tema).

Trasformazione necessaria: da testo grezzo a punteggio di sentiment servirà un modello NLP (a scelta più avanti: libreria di sentiment finanziario pre-addestrata, oppure un modello linguistico usato come classificatore).

### 4.4 Dati macroeconomici

Dato: tassi di interesse, inflazione (CPI), PIL, disoccupazione, indici di fiducia, calendario eventi macro.

Fonte: FRED (Federal Reserve Economic Data) — API gratuita e molto generosa (120 richieste/minuto), oltre 800.000 serie storiche USA e internazionali. È la fonte macro di riferimento per il progetto; per dati macro europei/italiani si valuterà Eurostat o Banca d'Italia in una fase successiva.

### 4.5 Nota generale sulle fonti

Nessuna fonte gratuita garantisce uptime/SLA. Per un progetto serio conviene: (a) mettere in cache locale ogni dato scaricato (evitare richieste ripetute), (b) prevedere fonti di fallback per i dati critici, (c) validare i dati alla ricezione (vedi sezione trasformazioni).

## 5. Strumenti

### 5.1 Linguaggio e librerie core

- Python come linguaggio principale.
- pandas / numpy per manipolazione dati e calcoli.
- yfinance, alpha_vantage o requests diretti per l'ingestion dati.
- matplotlib / plotly per grafici (plotly se si vuole interattività).
- Jupyter Notebook per l'esplorazione e i test, script .py per le parti da automatizzare/schedulare.

### 5.2 Librerie specialistiche (da valutare quando si arriva al modulo)

- Backtesting: librerie dedicate (es. backtrader, vectorbt, bt) oppure un motore di backtesting scritto su misura con pandas — da decidere in base alla complessità delle strategie.
- Metriche di rischio/performance: empyrical o calcoli custom (Sharpe, Sortino, max drawdown, VaR).
- NLP/sentiment: libreria di sentiment finanziario (es. modelli pre-addestrati tipo FinBERT) o classificazione via LLM.
- Statistica: scipy, statsmodels per test di significatività, correlazioni, regressioni.

### 5.3 Storage dati

Per iniziare: file locali (CSV/Parquet) organizzati per tipo di dato e data di download. Se il volume cresce: database locale (SQLite prima, poi eventualmente Postgres) per query più efficienti e per evitare di ricaricare tutto in memoria ogni volta.

### 5.4 Automazione e scheduling

Aggiornamento periodico dei dati (es. ogni sera dopo la chiusura dei mercati) e generazione report: da valutare uno scheduler (cron, oppure lo scheduling integrato di Cowork) una volta che il flusso è stabile.

### 5.5 Visualizzazione/reportistica

Notebook per analisi ad-hoc; dashboard HTML o foglio Excel per i report periodici da condividere/consultare (scelta da fare in base a chi deve leggere i report).

## 6. Architettura dati (pipeline)

1. Ingestion: script che scaricano dati grezzi da ciascuna fonte e li salvano "as-is" (raw), con timestamp di download, per poter sempre ricostruire cosa si sapeva in un certo momento (fondamentale per evitare look-ahead bias nel backtesting).
2. Pulizia e validazione: gestione valori mancanti, split/dividendi (aggiustamento prezzi), disallineamento di calendari tra mercati, rimozione duplicati, controllo outlier (es. prezzi a zero o errori di scala).
3. Normalizzazione: unificazione formati (valute, fusi orari, convenzioni di ticker) in uno schema dati comune condiviso dai tre moduli.
4. Feature engineering: calcolo di indicatori derivati — rendimenti giornalieri/log, volatilità storica, medie mobili, momentum, multipli fondamentali, punteggi di sentiment aggregati, variabili macro allineate temporalmente.
5. Storage strutturato: salvataggio dei dati puliti/derivati in formato pronto per essere interrogato dai moduli di screening, backtesting e monitoraggio.
6. Livello applicativo: le tre logiche (screening, backtest, monitoraggio) leggono dallo stesso layer di dati puliti, evitando duplicazione di codice per il calcolo delle metriche.

Questi sei passi sono, in pratica, il contenuto della Fase 1: la sezione 9 li trasforma in un piano operativo dettagliato.

## 7. Trasformazioni e analisi da implementare

### 7.1 Trasformazioni sui prezzi

Calcolo rendimenti (semplici e logaritmici), aggiustamento per dividendi/split, ricampionamento (giornaliero → settimanale/mensile), calcolo di medie mobili e indicatori tecnici di base (RSI, MACD, bande di volatilità) se rilevanti per lo screening/backtest.

### 7.2 Trasformazioni sui fondamentali

Normalizzazione dei multipli per settore (un P/E si giudica rispetto ai peer, non in assoluto), calcolo di tassi di crescita (utili, ricavi) su più periodi, punteggi compositi (es. media pesata di più metriche per generare uno "score" di qualità/valore).

### 7.3 Trasformazioni su news/sentiment

Aggregazione giornaliera del sentiment per titolo, calcolo di un indice di sentiment su finestra mobile, allineamento temporale con i prezzi (attenzione a non introdurre look-ahead: una notizia delle 18:00 non può spiegare il prezzo di chiusura delle 17:30 dello stesso giorno).

### 7.4 Trasformazioni sui dati macro

Allineamento delle serie macro (spesso mensili/trimestrali) con serie di prezzo (giornaliere) tramite forward-fill controllato, calcolo di sorprese macro (dato pubblicato vs atteso, se disponibile) come possibile fattore esplicativo.

### 7.5 Analisi di portafoglio (modulo monitoraggio)

Rendimento totale e annualizzato, rendimento per posizione e contributo al totale, volatilità e drawdown massimo, Sharpe/Sortino ratio, allocazione per settore/area/valuta, tracking error e beta rispetto a un benchmark, correlazione tra le posizioni (per capire la diversificazione reale).

### 7.6 Analisi di backtesting

Equity curve della strategia vs benchmark, metriche di rischio/rendimento (Sharpe, Sortino, Calmar, max drawdown, win rate), analisi di sensitività ai parametri della strategia, robustezza su sotto-periodi diversi (out-of-sample), stima dell'impatto di costi di transazione realistici.

### 7.7 Analisi di screening

Ranking dei titoli su score compositi, analisi di correlazione tra i criteri di selezione (per evitare di pesare più volte lo stesso fattore), backtest storico dello score stesso (lo score avrebbe funzionato in passato?), segmentazione per settore/dimensione per confronti equi.

## 8. Refresh rate consigliato per fonte

Progetto EOD (end-of-day), non near-real-time: il vincolo reale è Alpha Vantage, tutto il resto ha ampio margine.

- Prezzi (yfinance): 1 volta/giorno dopo la chiusura del mercato, in un'unica chiamata batch con tutti i ticker. Nessun limite ufficiale, e un refresh più frequente non serve per analisi EOD.
- Fondamentali (Alpha Vantage, 25 richieste/giorno, 5/min): refresh settimanale o mensile per ticker (cambiano poco). Se l'universo supera i 25 titoli, ruota un sottoinsieme al giorno. In alternativa, Finnhub (60/min) copre l'intero universo anche giornalmente.
- News/sentiment (NewsAPI, 100 richieste/giorno): 1 richiesta/giorno per ticker monitorato, limitando il fetch alla shortlist/portafoglio se l'universo è ampio (restare sotto ~80-90 ticker/giorno).
- Reddit (PRAW, 60 richieste/min ≈ 86.400/giorno): margine amplissimo; 1 volta/giorno è più che sufficiente per un progetto personale.
- Macro (FRED, 120 richieste/min): dati mensili/trimestrali alla fonte, quindi refresh settimanale non perde informazione.
- Google Trends (pytrends): libreria archiviata (aprile 2025), blocchi 429 frequenti e imprevedibili. Da evitare o usare con pochissime richieste/giorno e pause di alcuni secondi tra l'una e l'altra; da riconsiderare in futuro con l'API ufficiale Google Trends (ancora in alpha limitata).

Regola generale: cache locale di ogni dato scaricato con timestamp, così il progetto resta funzionante con l'ultimo dato buono anche se una fonte è irraggiungibile per un giorno.

## 9. Fase 1 in dettaglio — Fondamenta e Data Pipeline condivisa

### 9.0 Obiettivo e criterio di completamento

Obiettivo: avere, prima di scrivere una sola riga di logica di screening, un sistema che scarica, pulisce, normalizza e mette a disposizione in modo affidabile prezzi, fondamentali, news/sentiment grezzo e dati macro per un universo di titoli definito — così che screening, backtesting e monitoraggio possano *solo leggere* da questo layer, senza mai gestire ingestion o pulizia per conto proprio.

La Fase 1 è "completa e autosufficiente" quando, senza scrivere altro codice, è possibile:

1. Lanciare un unico comando/script che aggiorna tutti i dati dell'universo scelto.
2. Ottenere, per qualunque ticker dell'universo e qualunque data storica, prezzi puliti e aggiustati, gli ultimi fondamentali disponibili a quella data, e le variabili macro allineate — senza look-ahead bias.
3. Vedere un report di qualità dati che dice cosa ha funzionato e cosa no in quel run.
4. Riprodurre gli stessi risultati rieseguendo la pipeline (idempotenza).
5. Sopravvivere alla caduta di una fonte per uno o più giorni senza bloccarsi, usando l'ultimo dato buono in cache.

Se una di queste cinque cose non è vera, la Fase 1 non è ancora conclusa.

### 9.1 Struttura di progetto e repo

Struttura cartelle indicativa:

```
portafoglio/
  config/
    universe.yaml        # lista ticker, settore, borsa, valuta
    sources.yaml          # endpoint, rate limit, priorità/fallback per fonte
    .env                  # API key (mai in git)
  data/
    raw/<fonte>/<ticker>/<data_download>.parquet
    clean/<tipo_dato>/<ticker>.parquet
    features/<ticker>.parquet
    quality_reports/<data>.json
  src/
    ingestion/            # un modulo per fonte (prices_yf.py, fundamentals_av.py, news_newsapi.py, macro_fred.py, ...)
    cleaning/              # validazione, gestione mancanti, outlier, split/dividendi
    normalization/         # schema comune, mapping ticker, conversioni valuta/fuso orario
    features/              # rendimenti, volatilità, indicatori di base
    storage/               # lettura/scrittura parquet e/o SQLite
    orchestration/         # script che incatena i passi, gestione errori e log
  tests/
  notebooks/
  logs/
```

Repo su git da subito, con `.gitignore` che esclude `data/raw` e `data/clean` (dati pesanti e rigenerabili) e `.env` (segreti). Se si vuole comunque tracciare la storia dei dati grezzi, valutare DVC o git-lfs — non prioritario per un progetto personale, rimandabile a Fase 5.

### 9.2 Configurazione e segreti

- `config/universe.yaml`: lista esplicita di ticker con settore, borsa/mercato, valuta. Partire con un universo piccolo e gestibile (indicativamente 20-40 titoli) per validare tutta la pipeline end-to-end prima di scalare a centinaia di titoli — un universo grande amplifica subito i problemi di rate limit (specialmente Alpha Vantage) e rende più difficile isolare i bug.
- `config/sources.yaml`: per ogni fonte, endpoint, chiave di rate limit (richieste/minuto e/giorno), fonte di fallback in caso di errore.
- `.env` con le API key (Alpha Vantage, Finnhub, NewsAPI, FRED, credenziali Reddit/PRAW), caricato con `python-dotenv`. Nessuna chiave hardcoded nel codice o nei notebook.

### 9.3 Moduli di ingestion (uno per fonte)

Ogni modulo deve esporre un'interfaccia coerente (stessa firma di funzione, stesso formato di output raw) così che l'orchestratore possa trattarli in modo uniforme.

- **Prezzi (yfinance, fallback Finnhub/Alpha Vantage):** download batch di tutti i ticker in un'unica sessione, gestione esplicita dei ticker non trovati o rinominati/delistati, retry con backoff esponenziale sugli errori di rete, salvataggio raw partizionato per ticker e data di download.
- **Fondamentali (Alpha Vantage primario, Finnhub fallback):** rispetto rigoroso del rate limit (coda interna con throttling, non solo `time.sleep` a caso), rotazione dei ticker se l'universo supera le 25 richieste/giorno consentite, log esplicito di quali ticker sono stati aggiornati in quale run.
- **News/sentiment grezzo (NewsAPI, Finnhub news, PRAW Reddit):** salvataggio del testo grezzo con metadata completi (fonte, ticker associato, timestamp di pubblicazione, timestamp di download) — il punteggio di sentiment vero e proprio (NLP) è un passo successivo che può restare fuori dal perimetro stretto della Fase 1 se si vuole tenere la prima iterazione più leggera, ma il testo grezzo va comunque raccolto e conservato da subito.
- **Macro (FRED):** lista esplicita delle serie da tracciare con i relativi codici FRED (tassi, CPI, PIL, disoccupazione, indici di fiducia); download meno frequente, meno soggetto a errori vista la generosità dei rate limit.

Ogni modulo di ingestion non deve mai sovrascrivere un file raw già scaricato: si aggiunge un nuovo file con nuovo timestamp. Questo è ciò che permette, in Fase 3, di ricostruire esattamente "cosa si sapeva" in una data passata.

### 9.4 Layer di caching e gestione errori

Prima di ogni chiamata a una fonte esterna, un layer comune verifica: esiste già un dato scaricato di recente per questo ticker/fonte? è abbastanza fresco secondo la regola di refresh (sezione 8) da poter essere riusato senza nuova chiamata? Se la fonte fallisce (timeout, errore HTTP, rate limit superato), si usa l'ultimo dato buono in cache e si registra l'errore, senza interrompere l'intero run per gli altri ticker/fonti.

Logging strutturato per ogni tentativo di fetch: fonte, ticker, esito (successo/fallimento/da cache), timestamp, eventuale messaggio di errore. Questo log è la base del report di qualità dati (9.10).

### 9.5 Pulizia e validazione

- Valori mancanti: mai un fill silenzioso. Forward-fill solo dove giustificato (es. dati macro mensili proiettati su calendario giornaliero) e sempre con un flag che segnala "valore riportato, non originale".
- Split e dividendi: confronto tra prezzo raw e adjusted close, verifica coerenza dei fattori di aggiustamento.
- Outlier: controlli espliciti su prezzi a zero o negativi, variazioni percentuali giornaliere anomale, volumi a zero prolungati.
- Calendari: allineamento su un calendario di trading di riferimento (gestione festività e giorni di chiusura specifici per mercato/borsa).
- Deduplicazione: stesso ticker/data scaricato più volte (es. per retry) non deve produrre righe duplicate nel dataset clean.

### 9.6 Schema dati comune (normalizzazione)

Definire e documentare uno schema unico che tutte le fonti devono rispettare una volta pulite, ad esempio per i prezzi: `date (UTC o locale di mercato, coerente), ticker (identificatore univoco), open, high, low, close, adj_close, volume, currency`. Analogamente per fondamentali (metrica, valore, periodo di riferimento, data di pubblicazione) e per macro (serie, data, valore, unità).

Punti critici da risolvere qui e non lasciare impliciti: convenzione univoca di ticker tra fonti diverse (yfinance, Alpha Vantage e Finnhub a volte usano suffissi o naming diversi per lo stesso titolo), fuso orario esplicito, valuta originale sempre conservata (conversioni valutarie eventualmente come colonna aggiuntiva, non sostitutiva).

### 9.7 Feature engineering di base condiviso

Solo le feature davvero comuni a tutti e tre i moduli vanno calcolate qui: rendimenti semplici e logaritmici, volatilità storica su una o due finestre standard (es. 20/60 giorni). Vanno calcolate una sola volta nel layer dati e riusate, non ricalcolate in modo indipendente da screening/backtest/monitoraggio (rischio di incoerenze tra moduli). Feature più specifiche (score di screening, indicatori tecnici avanzati per una strategia di backtest) restano di competenza del modulo che le usa, non della Fase 1.

### 9.8 Storage

Formato parquet per prezzi/fondamentali/feature (efficiente, tipizzato, leggibile da pandas senza dipendenze pesanti), partizionato per fonte/ticker così da poter aggiornare un singolo ticker senza riscrivere tutto il dataset. SQLite valutabile da subito se si preferisce poter interrogare i dati con SQL nei notebook di esplorazione, senza però che sia un requisito bloccante della Fase 1.

Politica di retention: il raw si conserva integralmente (anche se pesante, è la fonte di verità storica); il clean e le feature sono sempre rigenerabili dal raw e quindi non serve conservarne infinite versioni.

### 9.9 Automazione minima

Un unico script (es. `run_daily_update.py`) che orchestra in sequenza: fetch da tutte le fonti (rispettando cache e rate limit) → pulizia/validazione → normalizzazione → feature di base → scrittura storage → generazione report di qualità. Eseguibile manualmente per tutta la Fase 1; la schedulazione automatica (cron o scheduling Cowork) è esplicitamente rimandata alla Fase 5, per non introdurre complessità di orchestrazione prima che la pipeline sia stabile.

### 9.10 Test e controllo qualità

- Test automatici minimi: ogni ticker dell'universo ha dati per la data attesa dopo un run, non ci sono NaN inattesi nelle colonne chiave, i prezzi rientrano in un range plausibile (es. nessun prezzo a 0 o con variazione giornaliera implausibile senza split/dividendo che lo giustifichi).
- Report di qualità generato ad ogni run: quanti ticker aggiornati con successo, quanti da cache, quanti falliti e per quale fonte/motivo. Questo report è ciò che permette di fidarsi (o non fidarsi) dei dati usati a valle in screening e backtest.

### 9.11 Criteri di uscita dalla Fase 1

Prima di iniziare la Fase 2 (Screening), verificare esplicitamente che:

1. L'universo iniziale scelto ha prezzi, fondamentali e macro disponibili e puliti per l'intero periodo storico necessario.
2. Un run completo della pipeline è riproducibile e produce lo stesso output a parità di dati raw.
3. Il report di qualità non segnala errori sistemici (es. una fonte sempre in fallimento) irrisolti.
4. Lo schema dati comune è stabile e documentato — cambiarlo dopo che screening/backtest lo usano ha un costo molto più alto che definirlo bene ora.
5. È chiaro, per ogni dato, a quale data era effettivamente disponibile (per evitare look-ahead nel backtesting).

### 9.12 Mappa del codice da scrivere: cosa, a cosa serve, come si collega alla Fase 2

Prima di scrivere codice, conviene fissare l'elenco dei moduli previsti, la funzione di ciascuno e — soprattutto — quale sarà il punto di contatto con la Fase 2 (Screening), in modo che quel confine resti stabile una volta iniziata la Fase 2.

**Configurazione**

- `src/config/loader.py`: carica `universe.yaml`, `sources.yaml` e `.env`. Scopo: unico punto di lettura della configurazione — nessun altro modulo apre direttamente questi file, così un domani cambiare formato di config non impatta tutto il codice.

**Ingestion (un modulo per fonte, stessa interfaccia per tutti)**

- `src/ingestion/prices_yf.py`: `fetch_prices(tickers, start, end)` → scarica OHLCV da yfinance e salva raw. Scopo: unico punto di contatto con yfinance.
- `src/ingestion/prices_fallback.py`: stessa interfaccia, ma su Finnhub/Alpha Vantage, usato quando yfinance fallisce.
- `src/ingestion/fundamentals_av.py` e `fundamentals_finnhub.py`: `fetch_fundamentals(tickers)` → bilanci, EPS, multipli, con gestione rate limit propria di ciascuna fonte.
- `src/ingestion/news_newsapi.py`, `news_finnhub.py`, `news_reddit.py`: `fetch_news(tickers)` → testo grezzo + metadata.
- `src/ingestion/macro_fred.py`: `fetch_macro(series_ids)` → serie macro.
- `src/ingestion/cache.py`: decide, prima di ogni chiamata, se serve una nuova richiesta o se il dato in cache è abbastanza fresco. Scopo: layer comune riusato da tutti i moduli sopra, così la logica di caching non si riscrive per ogni fonte.
- `src/ingestion/rate_limiter.py`: throttling generico parametrizzato sui limiti di ciascuna fonte (richieste/minuto, richieste/giorno).

**Pulizia**

- `src/cleaning/validators.py`: controlli su mancanti, outlier, duplicati.
- `src/cleaning/adjustments.py`: aggiustamento split/dividendi.
- `src/cleaning/calendar.py`: allineamento sui calendari di trading.

**Normalizzazione**

- `src/normalization/schema.py`: definizione formale (es. classi/pydantic) dello schema comune di sezione 9.6. Scopo: ogni dataset clean viene validato contro questo schema prima di essere salvato — è il "contratto" che tutto il resto del progetto può dare per scontato.
- `src/normalization/ticker_mapping.py`: mappa i naming di ticker divergenti tra fonti diverse su un identificatore unico.

**Feature di base**

- `src/features/returns.py`: rendimenti semplici/log.
- `src/features/volatility.py`: volatilità storica rolling su finestre standard.

**Storage**

- `src/storage/io.py`: funzioni di lettura/scrittura (parquet ed eventualmente SQLite). Scopo: **questo è il modulo che la Fase 2 userà davvero** — espone funzioni come `load_prices(tickers, start, end)`, `load_fundamentals(tickers, as_of_date)`, `load_macro(series_ids, start, end)`, `load_features(tickers, feature_names, start, end)`, `get_universe()`. Nessun altro dettaglio di come/dove sono salvati i dati deve trapelare fuori da questo modulo.

**Orchestrazione**

- `src/orchestration/run_daily_update.py`: script principale, incatena ingestion → pulizia → normalizzazione → feature → storage.
- `src/orchestration/quality_report.py`: genera il report di qualità dati di ogni run (sezione 9.10).

**Test**

- `tests/`: uno o più test per ciascun modulo sopra, più i test di integrità dati di sezione 9.10.

**A quale scopo, in sintesi:** ogni modulo di ingestion/pulizia/normalizzazione esiste per fare in modo che nessuno, in nessuna fase successiva, debba mai scaricare un dato grezzo, gestire un rate limit, o decidere come trattare un valore mancante. Quel lavoro si fa una volta sola qui.

**Collegamento con la Fase 2 (Screening):** il codice di screening (futuro `src/screening/`) dipenderà *solo* da `src/storage/io.py` (e, per le feature già pronte, da `src/features/`) — mai direttamente da ingestion, cleaning o normalization. In pratica lo screening chiamerà qualcosa come:

```
prices = load_prices(universe, start, end)
fundamentals = load_fundamentals(universe, as_of_date=data_di_analisi)
features = load_features(universe, ["return_1y", "volatility_60d"], start, end)
```

e costruirà sopra questi DataFrame i propri indicatori, filtri e score — senza mai preoccuparsi di dove vengano i dati o di come siano stati puliti. Questo confine netto è anche ciò che rende possibile, più avanti, sostituire una fonte dati (es. cambiare provider dei fondamentali) senza toccare una riga di codice di screening: basta che `src/storage/io.py` continui a restituire lo stesso schema.

Un secondo punto di collegamento, meno ovvio ma importante per il backtesting (Fase 3): `load_fundamentals(..., as_of_date=...)` deve restituire solo dati che erano effettivamente pubblicati a quella data (point-in-time), non l'ultimo dato disponibile oggi. È un requisito che va progettato nella Fase 1 (nello schema e nello storage), non aggiunto dopo — altrimenti la Fase 2 costruita su uno storage "naive" produrrà uno screening storicamente non riproducibile senza look-ahead bias.

## 10. Conclusioni

Prossimo passo operativo: eseguire la Fase 1 secondo il piano della sezione 9. Al termine, si valuteranno i risultati concreti (copertura dati, qualità, stabilità della pipeline) prima di dettagliare a sua volta la Fase 2 (Screening) con lo stesso livello di profondità, e si deciderà se/come estendere il progetto più avanti (nuovi mercati, nuove fonti dati, automazione completa, eventuale interfaccia per consultazione).
