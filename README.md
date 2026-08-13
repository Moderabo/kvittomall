# Automatisk generering av kvittomallar

Detta projekt automatiserar skapandet av kvittomallar för utläggsredovisning. Det hämtar data från ett Google Sheet (kopplat till ett Google Form), laddar ner länkade kvitton från Google Drive, och skapar en sammanställd PDF per utlägg.

## Arbetsflöde

Allt körs genom ett enda kommando, `kvittomall`, med fyra steg. Varje steg är idempotent: dess status för varje rad sparas i en lokal databas (`data/kvittomall_state.db`) och verifieras mot filsystemet vid varje körning, så avbrutna eller misslyckade körningar kan köras om utan att göra om onödigt arbete eller tappa data.

Vid `kvittomall run` körs alla fyra stegen även om ett tidigare steg misslyckas - t.ex. om **fetch** inte kan nå Google just då körs **download**/**process**/**generate** ändå, mot den `data/responses.csv` som redan finns sedan en tidigare lyckad hämtning. Ett borttaget PDF-utlägg vars data redan laddats ner och bearbetats byggs alltså om även om det Google-anropet skulle misslyckas. Vilka steg som misslyckades skrivs ut på slutet och finns i respektive stegs logg.

1. **fetch** - hämtar data från Google Sheet och sparar som `data/responses.csv`. Varnar (men stoppar inte) om en tidstämpel inte går att tolka eller om ordningen inte är kronologisk - inget annat steg förutsätter längre att raderna kommer i ordning.
2. **download** - laddar ner kvitton från Google Drive-länkarna i `data/responses.csv` till `data/downloads/`.
3. **process** - komprimerar bilder adaptivt och sparar dem som JPEG i `data/processed/`; PDF:er kopieras oförändrade.
4. **generate** - skapar de färdiga kvittomallarna i `final/`, uppdelat i `privat/`, `sektionskort/`, `milersättning/` och `övrigt/` (allt annat) baserat på `Transaktionstyp`.

Varje kategorimapp under `final/` innehåller alltid bara den senast genererade/uppdaterade omgången, så det är enkelt att se vad som är nytt. Så fort en ny omgång skapas arkiveras föregående omgångs filer till `final/previous/<kategori>/` istället för att skrivas över. En körning som inte hittar något nytt rör ingenting.

Allt under `data/` (nedladdningar, bearbetade filer, CSV:n, databasen, lock-filen) är arbetsdata som verktyget själv äger och kan bygga om från grunden - `final/` och `logs/` ligger däremot direkt i rotmappen, eftersom de är det du faktiskt vill åt: de färdiga rapporterna respektive loggarna.

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

1. **`.env`-fil** i projektets rotmapp, med Google Sheet-uppgifterna. [.env.example](.env.example) listar alla tillgängliga variabler (med sina standardvärden utkommenterade) och kan användas som utgångspunkt - kopiera den till `.env` och fyll i det som behövs:
    ```
    SHEET_ID="din_sheet_id_här"
    SHEET_GID="din_sheet_gid_här"
    ```

2. **Logotyp**: filen `LOGO` pekar på i [kvittomall/config.py](kvittomall/config.py) (standard: `logo.svg`) läggs till i övre högra hörnet på varje sida i de genererade PDF:erna - både på försättsbladet och på varje bifogat kvitto. Filen ska ligga i projektets rotmapp.
    - **SVG rekommenderas.** En riktig vektorlogotyp (inte en SVG som bara omsluter en rastrerad bild) blir dramatiskt mycket mindre i den färdiga PDF:en jämfört med samma logga som PNG/JPG - logotypen ritas om på varje sida, så skillnaden märks särskilt i kvitton med flera bilagor.
    - **PNG/JPG fungerar också** och väljs automatiskt om `LOGO` inte slutar på `.svg`. Använd då en tillräckligt hög upplösning för att inte bli suddig i utskrift - men räkna med betydligt större PDF-filer än med SVG.
    - **För att byta logga**: lägg filen i rotmappen och ändra `LOGO = "..."` i `config.py` till dess filnamn.

3. **Kvittomallens innehåll**: vilka fält som visas på försättsbladet styrs av `PDF_SECTIONS` i [kvittomall/config.py](kvittomall/config.py) - varje etikett mappas där till en kolumn i kalkylbladet.

4. **Kolumnnamn**: varje kolumnnamn verktyget letar efter (t.ex. `Tidstämpel`, `Namn`, `Summa`, `Körda mil`, `Kontonummer`, ...) är en egen namngiven konstant i `config.py`, med exakt sheet-kolumnens text som standardvärde. Om ett annat Google Form har en annan formulering på en fråga, sätt motsvarande `SHEET_COLUMN_*`-variabel i `.env` istället för att ändra i koden, t.ex.:
    ```
    SHEET_COLUMN_MIL="Antal mil"
    ```
    Se toppen av `config.py` för hela listan av `SHEET_COLUMN_*`-variabler och vilken standardtext de motsvarar.

5. **Bildkvalitet vid komprimering**: hur hårt uppladdade kvittobilder komprimeras innan de läggs in i PDF:en styrs av `IMAGE_QUALITY` - standardvärdet (`"normal"`) sätts i `config.py`, precis som kolumnnamnen ovan, och kan valfritt ändras direkt där eller överstyras per miljö via `.env`. Målet är alltid *läsbart*, aldrig *snyggt*. Fem lägen, från högst till lägst kvalitet:
    ```
    IMAGE_QUALITY="normal"   # extreme | high | normal (standard) | low | potato
    ```
    - `extreme` - ingen storleksändring eller kvalitetssänkning, bara anpassning till sidan.
    - `high` / `normal` / `low` - stegvis hårdare komprimering och nedskalning.
    - `potato` - skalas ner till 480px på långsidan och komprimeras hårt; använd bara om filstorlek är ett större problem än läsbarhet.

    Ändrar man `IMAGE_QUALITY` gäller det bara bilder som bearbetas *efter* ändringen - redan bearbetade filer i `data/processed/` räknas fortfarande som klara och byggs inte om automatiskt.

### Åtkomst till kalkylbladet och Drive: publikt eller service account

Verktyget kan hämta data på två sätt, styrt av `ACCESS_MODE` i `.env`:

- **`public`** - den ursprungliga metoden. Kalkylbladet måste vara delat som "Alla med länken", och samma sak för varje Drive-mapp/fil som kvitton laddas upp till. Enklast att komma igång med, men innebär att vem som helst med länken kan läsa alla svar och kvitton.
- **`api`** - hämtar via ett Google service account istället. Kalkylbladet och Drive-mappen kan då vara helt låsta (inte delade publikt) och bara delas med service accountets e-postadress. Kräver att ett service account skapas i Google Cloud Console (aktivera Sheets API och Drive API, skapa ett service account, ladda ner en JSON-nyckel) och att den e-postadressen ges läsbehörighet ("Viewer") till både kalkylbladet och Drive-mappen.
- **Ej satt, eller `auto`** (standard) - försöker `api` först; om det misslyckas (t.ex. saknad nyckel eller inte delat med service accountet) varnas det i loggen och det faller tillbaka till `public` för den körningen. Om båda misslyckas avslutas programmet med ett fel.

Vid `api` eller `auto` behövs även:
```
GOOGLE_SERVICE_ACCOUNT_FILE="/sökväg/till/service-account-nyckel.json"
```
Håll nyckelfilen utanför git (den ska inte checkas in) och begränsa dess filrättigheter - den fungerar som ett lösenord till service accountet. Vem som helst med filen kan läsa allt som delats med service accountets e-postadress (hela kalkylblad, inte bara den flik verktyget faktiskt läser, och hela Drive-mappar, inte bara kvittona) - dela den därför bara med de som behöver den, och dela bara den specifika kalkylbladsfilen och den specifika kvittomappen med service accountet, inte t.ex. en överordnad mapp eller hela Drive.

#### Hitta och hantera nyckeln i Google Cloud Console

1. Gå till [console.cloud.google.com](https://console.cloud.google.com) och logga in med det Google-konto som administrerar projektet (t.ex. skattmästarens).
2. Öppna menyn längst upp till vänster och välj **APIs & Services** (API:er och tjänster).
3. Välj **kvittomall** som projekt - härifrån ser man allt som hör till verktyget (aktiverade API:er, service accountet, nycklar).
4. Välj **Credentials** (Autentiseringsuppgifter) i vänstermenyn.
5. Klicka på service accountet i listan för att se dess detaljer och senaste aktivitet.
6. Välj fliken **Keys** (Nycklar). Här listas alla nycklar som skapats för service accountet - härifrån skapas nya (ner som JSON), och gamla tas bort (återkallas/revoke:as).

#### Nyckelhantering - skattmästarens val

Det finns ingen enda rätt modell, bara en avvägning mellan säkerhet och enkelhet:

- **En delad nyckel för alla** - samma JSON-fil delas med alla som behöver köra verktyget, oavsett om de jobbar mot olika Google Forms eller inte.
- **En nyckel per Google Form** - separat nyckel per formulär/kalkylblad.
- **En nyckel per person** - var och en som kör verktyget får sin egen nyckel.

Nycklar kan när som helst återkallas i Google Cloud Console, t.ex. när någon lämnar sin post - eller så litar man på att personen själv tar bort filen från sin dator när den inte längre behövs.

**Rekommendation**: en delad nyckel för alla, även över flera Google Forms. Vid terminens/verksamhetsårets slut återkallas nyckeln och en ny skapas och delas med den nya styrelsen/kassörerna. Det balanserar säkerhet (nyckeln lever aldrig längre än en post) mot enkelhet (ingen löpande administration av vem som har vilken nyckel under året).

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

- `final/privat/`, `final/sektionskort/`, `final/milersättning/`, `final/övrigt/` - senaste omgången, det som är nytt.
- `final/previous/privat/`, `final/previous/sektionskort/`, `final/previous/milersättning/`, `final/previous/övrigt/` - allt äldre.

Om en fil tas bort av misstag men innehållet på kalkylbladet inte ändrats, återskapas den vid nästa körning på exakt samma plats den låg på (kategorimappen eller `previous/`) - det räknas inte som nytt och flyttar inget annat.

## Utveckling

```sh
pip install -r requirements-dev.txt                     # lägger till pytest + pytest-cov ovanpå requirements.txt
pytest                                                    # kör hela testsviten (tests/)
pytest --cov=kvittomall --cov-report=term-missing         # samma, men med en rad per fil som visar hur stor andel som testas
```

Testerna rör aldrig de riktiga `data/`/`final/`/`logs/`-mapparna - de körs mot temporära kataloger. En [GitHub Actions](.github/workflows/tests.yml)-workflow kör samma sak (inklusive täckningsrapporten) på varje push/PR - resultatet syns i det körningens logg på fliken "Actions" på GitHub.

Täckningen är inte heltäckande med avsikt: ren logik och de säkerhetskontroller som avgör om något redan är klart (`rowkey.py`, `db.py`, `config.py`, med flera) är väl testade, medan de faktiska nätverksanropen mot Google Sheets/Drive och `cli.py`s kommandoradshantering inte är det - se `CLAUDE.md` för den fullständiga motiveringen.
