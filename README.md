# Automatisk generering av kvittomallar

Detta projekt automatiserar skapandet av kvittomallar för utläggsredovisning. Det hämtar data från ett Google Sheet (kopplat till ett Google Form), laddar ner länkade kvitton från Google Drive, och skapar en sammanställd PDF per utlägg.

## Arbetsflöde

Allt körs genom ett enda kommando, `kvittomall`, med fyra steg. Varje steg är idempotent: dess status för varje rad sparas i en lokal databas (`kvittomall_state.db`) och verifieras mot filsystemet vid varje körning, så avbrutna eller misslyckade körningar kan köras om utan att göra om onödigt arbete eller tappa data.

Vid `kvittomall run` körs alla fyra stegen även om ett tidigare steg misslyckas - t.ex. om **fetch** inte kan nå Google just då körs **download**/**process**/**generate** ändå, mot den `responses.csv` som redan finns sedan en tidigare lyckad hämtning. Ett borttaget PDF-utlägg vars data redan laddats ner och bearbetats byggs alltså om även om det Google-anropet skulle misslyckas. Vilka steg som misslyckades skrivs ut på slutet och finns i respektive stegs logg.

1. **fetch** - hämtar data från Google Sheet och sparar som `responses.csv`. Varnar (men stoppar inte) om en tidstämpel inte går att tolka eller om ordningen inte är kronologisk - inget annat steg förutsätter längre att raderna kommer i ordning.
2. **download** - laddar ner kvitton från Google Drive-länkarna i `responses.csv` till `downloads/`.
3. **process** - komprimerar bilder adaptivt och sparar dem som JPEG i `processed/`; PDF:er kopieras oförändrade.
4. **generate** - skapar de färdiga kvittomallarna i `final/`, uppdelat i `privat/`, `sektionskort/` och `övrigt/` (allt annat) baserat på `Transaktionstyp`.

Varje kategorimapp under `final/` innehåller alltid bara den senast genererade/uppdaterade omgången, så det är enkelt att se vad som är nytt. Så fort en ny omgång skapas arkiveras föregående omgångs filer till `final/previous/<kategori>/` istället för att skrivas över. En körning som inte hittar något nytt rör ingenting.

## Kom igång

Verktyget fungerar på **Linux**, **macOS** och **Windows** (via WSL). Instruktionerna nedan är gemensamma för alla tre - kör dem i en vanlig terminal (på Windows: inuti WSL, inte PowerShell/CMD).

### 1. Förutsättningar

| | Python 3.10+ | libmagic (filtypsigenkänning) |
|---|---|---|
| **Ubuntu / Debian / WSL** | Oftast redan installerat. Om `python3 -m venv` misslyckas: `sudo apt-get install python3 python3-venv python3-pip` | `sudo apt-get install libmagic-dev` |
| **macOS** | `brew install python@3.12` (Pythonen som följer med macOS är för gammal) | `brew install libmagic` |
| **Windows** | Installera [WSL](https://learn.microsoft.com/windows/wsl/install) (Ubuntu), öppna en WSL-terminal och följ Ubuntu-raderna ovan | Se Ubuntu-raden ovan, inuti WSL |

`libmagic` behövs på **alla tre** plattformarna - det är bara installationskommandot som skiljer sig. Utan det ger `python-magic` (som används för att avgöra vilken filtyp ett nedladdat kvitto är) ett importfel.

### 2. Klona och installera

```sh
git clone https://github.com/Moderabo/kvittomall.git
cd kvittomall

python3 -m venv venv
source venv/bin/activate      # Windows/WSL och macOS: samma kommando i WSL/bash

pip install -r requirements.txt
```

`requirements.txt` har fasta versionsnummer för att alla ska köra exakt samma, testade paket.

## Konfiguration

1. **`.env`-fil** i projektets rotmapp, med Google Sheet-uppgifterna:
    ```
    SHEET_ID="din_sheet_id_här"
    SHEET_GID="din_sheet_gid_här"
    ```

2. **`logo.png`** i rotmappen. Läggs till på varje sida i de genererade PDF:erna.

3. **Kvittomallens innehåll**: vilka fält som visas på försättsbladet styrs av `PDF_SECTIONS` i [kvittomall/config.py](kvittomall/config.py) - varje etikett mappas där till en kolumn i kalkylbladet.

### Åtkomst till kalkylbladet och Drive: publikt eller service account

Verktyget kan hämta data på två sätt, styrt av `ACCESS_MODE` i `.env`:

- **`public`** - den ursprungliga metoden. Kalkylbladet måste vara delat som "Alla med länken", och samma sak för varje Drive-mapp/fil som kvitton laddas upp till. Enklast att komma igång med, men innebär att vem som helst med länken kan läsa alla svar och kvitton.
- **`api`** - hämtar via ett Google service account istället. Kalkylbladet och Drive-mappen kan då vara helt låsta (inte delade publikt) och bara delas med service accountets e-postadress. Kräver att ett service account skapas i Google Cloud Console (aktivera Sheets API och Drive API, skapa ett service account, ladda ner en JSON-nyckel) och att den e-postadressen ges läsbehörighet ("Viewer") till både kalkylbladet och Drive-mappen.
- **Ej satt, eller `auto`** (standard) - försöker `api` först; om det misslyckas (t.ex. saknad nyckel eller inte delat med service accountet) varnas det i loggen och det faller tillbaka till `public` för den körningen. Om båda misslyckas avslutas programmet med ett fel.

Vid `api` eller `auto` behövs även:
```
GOOGLE_SERVICE_ACCOUNT_FILE="/sökväg/till/service-account-nyckel.json"
```
Håll nyckelfilen utanför git (den ska inte checkas in) och begränsa dess filrättigheter - den fungerar som ett lösenord till service accountet.

## Användning

Terminalen visar bara varningar, fel och en förloppsindikator. All detaljerad aktivitet loggas till `logs/{steg}-{år}-{månad}.log` (t.ex. `logs/download-2026-07.log`) - en fil per steg och månad, så loggarna aldrig växer obegränsat.

```sh
python -m kvittomall run       # Kör alla fyra steg i ordning
```

Eller ett steg i taget:

```sh
python -m kvittomall fetch      # 1. Hämta CSV-data
python -m kvittomall download   # 2. Ladda ner kvitton
python -m kvittomall process    # 3. Bearbeta filer
python -m kvittomall generate   # 4. Skapa PDF:er
```

Se aktuell status för alla rader - hämtat/nedladdat/bearbetat/genererat, var varje PDF ligger, och eventuella fel:

```sh
python -m kvittomall status
```

De färdiga rapporterna hamnar i:

- `final/privat/`, `final/sektionskort/`, `final/övrigt/` - senaste omgången, det som är nytt.
- `final/previous/privat/`, `final/previous/sektionskort/`, `final/previous/övrigt/` - allt äldre.

Om en fil tas bort av misstag men innehållet på kalkylbladet inte ändrats, återskapas den vid nästa körning på exakt samma plats den låg på (kategorimappen eller `previous/`) - det räknas inte som nytt och flyttar inget annat.
