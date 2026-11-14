# Comparatore Quote ⚽ (bookmaker italiani)

Sito statico (sfondo nero, mobile friendly) che confronta le quote del calcio dei **bookmaker italiani ADM**
prese dalle versioni **italiane** di:

- **Oddschecker Italia** – https://www.oddschecker.com/it/ (tutti i mercati, ~20-26 bookmaker)
- **Oddspedia Italia** – https://oddspedia.com/it/ (sezione separata, mercati principali, bookmaker filtrati per l'Italia)

Sito: https://edbass1.github.io/comparatore-quote/

## Funzioni
- Home: partite raggruppate per giorno e competizione (Serie A, Serie B, Coppa Italia,
  Champions, Europa e Conference League, Premier League, LaLiga, Bundesliga, Ligue 1, Eredivisie),
  ricerca squadra con suggerimenti, filtro per competizione, migliori quote 1X2.
  Oddspedia guarda i prossimi 6 giorni; Oddschecker i prossimi 13, così il turno successivo
  resta visibile nello stacco per le nazionali.
- **Classifica rating**: per ogni bookmaker, le 30 partite col mercato dal payout più alto
  (solo le sue quote, solo esiti completi). Clic sulla riga per gli altri mercati della partita.
- **UP / DOWN**: i 30 movimenti di quota più forti su 1X2 e Over/Under 2.5 nelle ultime 24 ore.
  Lo storico (`stato/storico-quote.json`) si accumula a ogni aggiornamento e la sezione si riempie
  dopo circa un giorno.
- Pagina partita: tabella quote per bookmaker con la **quota migliore evidenziata**, ricerca del tipo di scommessa,
  menu a tendina con tutti i mercati e pulsanti rapidi (1X2, Doppia chance, U/O 0.5–4.5, Goal/NoGoal,
  Risultato esatto, Primo/Secondo tempo, Parziale/Finale, Handicap, DNB, Combo, Marcatori, Corner, Cartellini…).
- Pulsanti flottanti **+ / −** (dimensione testo, ricordata nel browser) e **↑ torna su**.

## Aggiornamento automatico
Il workflow `.github/workflows/aggiorna.yml` gira **ogni 30 minuti** su GitHub Actions (solo `GITHUB_TOKEN`, nessun segreto),
scarica le quote con `scraper/fetch.py`, pubblica il sito su GitHub Pages e fa un piccolo commit di
`stato/ultimo-aggiornamento.json` (così GitHub non disattiva la pianificazione dopo 60 giorni di inattività).
I dati delle quote non vengono salvati nella cronologia git (vengono pubblicati direttamente su Pages).

Se una fonte non risponde, il sito lo dice chiaramente ("non disponibile"); se l'aggiornamento precedente ha meno di 6 ore
vengono mostrati quei dati con un avviso evidente. Nessuna quota viene inventata.

Avvio manuale: tab **Actions → Aggiorna quote → Run workflow**.

Le quote possono variare in ogni momento: verifica sempre sul sito del bookmaker. 18+ · Il gioco può causare dipendenza.
