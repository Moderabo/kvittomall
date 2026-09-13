# Automatisk generering av kvittomallar

Detta projekt automatiserar skapandet av kvittomallar för utläggsredovisning. Det hämtar data från ett Google Sheet (kopplat till ett Google Form), laddar ner länkade kvitton från Google Drive, och skapar en sammanställd PDF per utlägg.

## Arbetsflöde

Allt körs genom ett enda kommando, `kvittomall`, med fyra steg. Varje steg är idempotent: dess status för varje rad sparas i en lokal databas (`data/kvittomall_state.db`) och verifieras mot filsystemet vid varje körning, så avbrutna eller misslyckade körningar kan köras om utan att göra om onödigt arbete eller tappa data.

Vid `kvittomall run` körs alla fyra stegen även om ett tidigare steg misslyckas - t.ex. om **fetch** inte kan nå Google just då körs **download**/**process**/**generate** ändå, mot den `data/responses.csv` som redan finns sedan en tidigare lyckad hämtning. Ett borttaget PDF-utlägg vars data redan laddats ner och bearbetats byggs alltså om även om det Google-anropet skulle misslyckas. Vilka steg som misslyckades skrivs ut på slutet och finns i respektive stegs logg.

1. **fetch** - hämtar data från Google Sheet och sparar som `data/responses.csv`. Varnar (men stoppar inte) om en tidstämpel inte går att tolka eller om ordningen inte är kronologisk - inget annat steg förutsätter längre att raderna kommer i ordning.
2. **download** - laddar ner kvitton från Google Drive-länkarna i `data/responses.csv` till `data/downloads/`.
3. **process** - komprimerar bilder adaptivt och sparar dem som JPEG i `data/processed/`; PDF:er kopieras oförändrade.
4. **generate** - skapar de färdiga kvittomallarna i `final/`, uppdelat i `privat/`, `sektionskort/`, `milersättning/` och `övrigt/` (allt annat) baserat på `Transaktionstyp`.

Varje kategorimapp under `final/` samlar alla genererade PDF:er som ännu inte är granskade - mappen rensas inte automatiskt bara för att en ny PDF tillkommer. När du är klar med ett utlägg kör du `kvittomall handled` (se nedan), som flyttar filen till `final/handled/<kategori>/`. Ändras kalkylbladsraden igen efter det plockas den automatiskt tillbaka till den aktiva kategorimappen nästa gång `generate` körs.

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

### Alternativ: Docker

Slipper man installera Python/`libmagic` lokalt genom att köra allt i en container istället - `Dockerfile`/`docker-compose.yml` i repot bygger en image med allt förinstallerat.

```sh
git clone https://github.com/Moderabo/kvittomall.git
cd kvittomall

cp .env.example .env               # fyll i SHEET_ID/SHEET_GID som vanligt, se Konfiguration nedan
mkdir -p secrets                   # lägg din service account-nyckel här, sätt sedan
                                    # GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/<filnamn>.json i .env

docker compose up -d --build
```

Webbgränssnittet finns sedan på `http://localhost:5000` (eller den port du satt `WEBUI_PORT` till i `.env` - `docker-compose.yml` läser samma fil både för att skicka in miljövariabler i containern och för att avgöra vilken värdport som mappas). Ett enskilt kommando istället för webbgränssnittet körs som `docker compose run --rm kvittomall fetch` (byt `fetch` mot vilket kommando som helst, t.ex. `status` eller `run`).

`docker-compose.yml` binder `data/`, `final/`, `logs/`, `secrets/` (skrivskyddad) samt hela mappen `config/` (där `/config`-sidans sparade PDF-layout/kolumnmappning hamnar, se `paths.py`) till motsvarande sökvägar i containern, så inget av det här försvinner mellan omstarter. `config/` binds som en hel mapp och inte som enskilda filer, av en konkret anledning: att bindmounta en enskild fil direkt hade fått varje sparning från webbgränssnittet att krascha (appen skriver dessa filer atomiskt via en temp-fil som sedan byter namn till den riktiga, och kärnan vägrar byta namn rakt över en aktiv bindmount-punkt) - verifierat, inte antaget. Med hela mappen bunden fungerar det direkt utan några förberedande steg.

Containern kör som root - en medveten avvägning för enkelhetens skull (samma sorts avvägning som webbgränssnittets avsaknad av inloggning, se Webbgränssnitt nedan), men det betyder att filer som skapas under `data/`/`final/`/`logs/` på värdmaskinen ägs av root, inte din vanliga användare - `sudo` kan behövas för att titta i eller ta bort dem direkt från värden, om du någon gång behöver det.

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

3. **Kvittomallens innehåll**: vilka fält som visas på försättsbladet, i vilken ordning, och vilken kalkylbladskolumn varje etikett hämtar sitt värde från, går numera att ändra utan att röra kod - se "Redigera kvittomallens innehåll" under Webbgränssnitt nedan. `default_pdf_sections()` i [kvittomall/config.py](kvittomall/config.py) är fortfarande standardlayouten en ny installation startar med.

4. **Kolumnnamn**: varje kolumnnamn verktyget letar efter (t.ex. `Tidstämpel`, `Namn`, `Summa`, `Körda mil`, `Kontonummer`, ...) är en egen namngiven konstant i `config.py`, med exakt sheet-kolumnens text som standardvärde. Om ett annat Google Form har en annan formulering på en fråga finns nu tre sätt att rätta det, i denna prioritetsordning:
    1. **Webbgränssnittets kolumnmappning**, på "Configuration"-sidan (se "Rätta kolumnnamn" under Webbgränssnitt nedan) - enklast, och den enda som visar ett exempelvärde från kalkylbladet så man kan se att mappningen faktiskt är rätt innan den sparas.
    2. **`SHEET_COLUMN_*`-variabel i `.env`**, t.ex.:
        ```
        SHEET_COLUMN_MIL="Antal mil"
        ```
    3. **Standardvärdet i koden** (`config.py`), om ingen av ovanstående är satt.

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

Vart och ett av `download`, `process`, `generate` och `run` kan även begränsas till en enda rad genom att ange dess radnyckel (tidstämpeln, samma som visas i `status` eller webb-UI:ts lista) som extra argument, t.ex. `python -m kvittomall generate "2026-01-01 10.00.00"`.

När ett utlägg är granskat och klart markerar du det som hanterat, vilket flyttar dess PDF från kategorimappen till `final/handled/<kategori>/`. Kör kommandot igen på samma rad för att ångra - det växlar (toggle) mellan hanterat och ohanterat:

```sh
python -m kvittomall handled "2026-01-01 10.00.00"   # markerar (eller avmarkerar) en specifik rad
python -m kvittomall handled                          # markerar alla ännu ej hanterade rader (växlar inte tillbaka)
```

Vill du ta bort en rads filer från disk utan att röra CSV:n, databasen eller listan över rader - t.ex. för att frigöra utrymme eller tvinga fram en helt ny nedladdning/bearbetning/generering - använd `remove`. Till skillnad från övriga kommandon ovan krävs alltid en radnyckel (ingen "ta bort allt"-variant):

```sh
python -m kvittomall remove "2026-01-01 10.00.00"                    # tar bort alla filer för raden
python -m kvittomall remove "2026-01-01 10.00.00" --only downloads   # bara de nedladdade originalen
python -m kvittomall remove "2026-01-01 10.00.00" --only processed   # bara de bearbetade bilderna
python -m kvittomall remove "2026-01-01 10.00.00" --only final       # bara den färdiga PDF:en
```

Se aktuell status för alla rader - hämtat/nedladdat/bearbetat/genererat/hanterat, var varje PDF ligger, och eventuella fel:

```sh
python -m kvittomall status
```

De färdiga rapporterna hamnar i:

- `final/privat/`, `final/sektionskort/`, `final/milersättning/`, `final/övrigt/` - genererat men inte markerat hanterat än.
- `final/handled/privat/`, `final/handled/sektionskort/`, `final/handled/milersättning/`, `final/handled/övrigt/` - markerat hanterat med `kvittomall handled`.

Om en fil tas bort (av misstag, eller med `remove`) men innehållet på kalkylbladet inte ändrats, återskapas den vid nästa körning på exakt samma plats den låg på (kategorimappen eller `handled/`) - det räknas inte som nytt och flyttar inget annat.

### Webbgränssnitt

Som ett alternativ till kommandona ovan finns en lokal webbsida med samma funktioner:

```sh
python -m kvittomall webui
```

Öppna sedan `http://127.0.0.1:5000` i webbläsaren. Verktyget lyssnar som standard bara på den egna maskinen (`127.0.0.1`) - i linje med att detta är tänkt som ett lokalt verktyg man startar, använder och stänger ner igen, inte en server som ska stå exponerad. Körs verktyget på en server/VM och ska nås från en annan dator (t.ex. över nätverket eller via SSH till maskinen), sätt `WEBUI_HOST="0.0.0.0"` i `.env` för att lyssna på alla nätverksgränssnitt istället - då nås det via `http://<serverns-ip>:5000`. Adress och port kan ändras via `WEBUI_HOST`/`WEBUI_PORT` i `.env`, se [.env.example](.env.example).

Instrumentpanelen har samma knappar som kommandona ovan (fetch/download/process/generate/run/markera hanterat), och en sorterbar, filtrerbar lista över alla rader direkt under knapparna - klicka på kolumnrubrikerna för att sortera, eller skriv i filterfälten under varje rubrik för att bara visa matchande rader (flera filter kombineras - t.ex. ett utskott och status "handled" samtidigt). Klicka på en rad för att se dess kvitton, bearbetade bilder, färdiga PDF och alla andra ifyllda fält från kalkylbladet, samt köra/ta bort filer för/markera hanterat på just den raden.

**Ingen inloggning krävs** - med standardinställningen (`127.0.0.1`) spelar det mindre roll, eftersom bara den egna maskinen kan nå adressen. Men om `WEBUI_HOST` vidgas till `0.0.0.0` kan vem som helst som når adressen, t.ex. alla på samma nätverk, trigga körningar och ta bort filer - lämpligt på ett förtroendefullt hemma-/kontorsnätverk, men exponera aldrig porten mot internet utan att lägga till någon form av autentisering först.

Den röda **"Rensa allt"**-knappen längst ner återställer `data/` och `final/` helt - databasen, alla nedladdade/bearbetade filer och alla genererade PDF:er (hanterade eller ej) raderas permanent, och samma tomma mappstruktur som vid en helt ny installation skapas igen. `logs/` rörs inte. Detta går inte att ångra, och knappen ber alltid om bekräftelse innan den kör. Motsvarande kommando finns inte i terminalen - det är medvetet bara tillgängligt via webbgränssnittet.

#### Redigera kvittomallens innehåll

Länken **"PDF layout"** på instrumentpanelen öppnar sidan **"Configuration"** (`/config`), som PDF-layouten och kolumnmappningen delar - de är två vyer av samma sak (vilken kolumn ger vilken information), så att fixa en mappning i den ena hänger oftast ihop med den andra. Överst i PDF-layout-delen finns en kort förklaring av vad varje kontroll gör:

- **Textrutan** - etiketten som skrivs ut på PDF:en direkt före värdet (t.ex. "Datum:").
- **Kolumn-rullistan** - vilken uppgift från kalkylbladet som fyller i värdet.
- **Format-rullistan** - en suffix som läggs till efter värdet: `kr` för ett belopp, `mil` för en sträcka, eller `None` för vanlig text utan suffix.

Försättsbladets fält kan redigeras utan att röra kod: byta vilken kolumn en etikett hämtar sitt värde från, ändra etikettens text, lägga till eller ta bort fält och rader/sektioner, samt ändra ordningen (pilarna flyttar ett fält upp/ner eller till en annan sektion). Ändringen börjar gälla direkt vid nästa `generate` - både från webbgränssnittet och terminalen, ingen omstart krävs.

Varje fälts kolumn-rullista visar bara de kolumner verktyget faktiskt känner till med namn - en per inställning i kolumnmappningen längre ner på samma sida (t.ex. "Sum: Summa") - inte vilken kalkylbladskolumn som helst. En kolumn som bara finns med av submittern egen anledning (utan någon egen namngiven inställning, t.ex. en egen bekräftelseruta) går alltså inte längre att välja här; ge den ett namn i kolumnmappningen först om den ska synas på PDF:en.

Tidstämpeln, vilken kolumn kvittolänkarna ligger i, och vilken kolumn styr kategorimappen kan **inte** väljas här - de avgör radens identitet, vilka bilagor som hittas, och vilken `final/`-mapp PDF:en hamnar i, inte bara vad som visas på sidan. De ändras i kolumnmappningen istället, se nästa avsnitt.

Om ett fälts val är markerat "not found in last fetch" beror det på att den riktiga kolumnrubriken i kalkylbladet inte matchar det som är konfigurerat - rätta det i kolumnmappningen, inte här.

Ett fält som pekar på en tom kolumn för en viss rad hoppas alltid över på just den PDF:en - alla fält behöver inte finnas ifyllda på varje rad. Om en kalkylbladskolumn däremot har ett värde men inte är kopplad till något fält alls, visas en liten ⚠-ikon direkt bredvid just det fältet på radens sida (håll muspekaren över den för en förklaring), samt en rad i loggen vid `generate` - inte ett fel, bara en påminnelse om att informationen inte kommer med på PDF:en. Varningen är per rad: en tom `Körda mil` på ett vanligt utlägg (inte reseersättning) varnar aldrig, eftersom det är helt normalt att den kolumnen är tom då.

#### Rätta kolumnnamn

Kolumnmappningen, längre ner på samma **"Configuration"**-sida, visar, för varje sak verktyget behöver från kalkylbladet (tidstämpel, namn, kvittolänkar, transaktionstyp, summa, datum, ...), vilken kolumnrubrik den för närvarande är kopplad till - och en rullista med de kolumnrubriker som faktiskt finns i det senast hämtade kalkylbladet att välja bland istället. Första valet i varje rullista är alltid **"(Default)"** - det lämnar inställningen orörd (den fortsätter styras av `.env` eller det inbyggda standardvärdet) istället för att peka på en specifik kolumn, vilket är rätt val för en kolumn som helt enkelt inte finns i just detta kalkylblad (t.ex. körda mil för ett utskott som aldrig ger reseersättning). Ett exempelvärde från första hämtade raden visas bredvid varje val, så man kan se att mappningen faktiskt stämmer innan den sparas - detta går inte att avgöra automatiskt, så det är upp till en själv att kontrollera.

Tidstämpel, kvittolänkar och transaktionstyp visas i en egen sektion med en tydlig varning: ändras någon av dem efter att rader redan finns i systemet hittas inte de gamla raderna längre under sin nya identitet vid nästa `fetch` - redan nedladdade/bearbetade filer och PDF:er raderas inte, men blir övergivna (inte längre kopplade till något) tills de bearbetas om under den nya mappningen. Samma risk finns redan idag vid manuell redigering av `.env` - varningen är ny, risken är det inte.

Ändringar börjar gälla direkt, precis som PDF-layouten ovan - ingen omstart av `kvittomall webui` eller terminalkommandona krävs.

Om PDF-layouten redan har sparats en gång (även bara för att ändra ordningen på fälten) har varje fält frusits till den kolumn det då pekade på - det är inte längre kopplat live till t.ex. `DATUM`-inställningen. Att rätta en felaktig mappning här flyttar därför automatiskt med sig alla redan sparade fält som pekade på den gamla kolumnen, så en tidigare sparad layout inte fortsätter peka på fel kolumn i tysthet. Ett fält som pekar på en helt egen, fritt vald kolumn (som inte motsvarar någon av inställningarna ovan) påverkas aldrig av detta.

## Utveckling

```sh
pip install -r requirements-dev.txt                     # lägger till pytest + pytest-cov ovanpå requirements.txt
pytest                                                    # kör hela testsviten (tests/)
pytest --cov=kvittomall --cov-report=term-missing         # samma, men med en rad per fil som visar hur stor andel som testas
```

Testerna rör aldrig de riktiga `data/`/`final/`/`logs/`-mapparna - de körs mot temporära kataloger. En [GitHub Actions](.github/workflows/tests.yml)-workflow kör samma sak (inklusive täckningsrapporten) på varje push/PR - resultatet syns i det körningens logg på fliken "Actions" på GitHub.

Täckningen är inte heltäckande med avsikt: ren logik och de säkerhetskontroller som avgör om något redan är klart (`rowkey.py`, `db.py`, `config.py`, med flera) är väl testade, medan de faktiska nätverksanropen mot Google Sheets/Drive och `cli.py`s kommandoradshantering inte är det - se `CLAUDE.md` för den fullständiga motiveringen.
