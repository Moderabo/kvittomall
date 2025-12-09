# Automatisk generering av kvittomallar

Detta projekt automatiserar skapandet av kvittomallar. Det hämtar data från ett Google Sheet, validerar datan, laddar ner länkade kvitton från Google Drive och skapar en sammanställd PDF för varje utlägg.

## Arbetsflöde

Processen är uppdelad i fyra steg. Utdata från ett steg blir indata för nästa.
1.  **Hämta CSV-data**: Data hämtas från ett specificerat Google Sheet och sparas som `responses.csv`. Skriptet validerar även att tidsstämplarna är i kronologisk ordning.
2.  **Ladda ner kvitton**: Filer från Google Drive-länkar i `responses.csv` sparas i mappen `downloads/`.
3.  **Bearbeta filer**: Bilder konverteras till JPG, alla filer standardiseras och sparas i mappen `processed/`.
4.  **Skapa PDF:er**: Färdiga kvittomallar skapas från filerna i `processed/` och sparas i mappen `final/`.

## Förutsättningar

- **Python 3.7+**
- **Poppler**: Krävs för PDF-hantering.
  - **macOS**: `brew install poppler`
  - **Ubuntu/Debian**: `sudo apt-get install poppler-utils`
  - **Windows**: Ladda ner Poppler och lägg till dess `bin/`-mapp i din PATH.

## Installation

1.  **Klona projektet:**
    ```sh
    git clone https://github.com/Moderabo/kvittomall.git
    cd kvittomall
    ```

2.  **Skapa en virtuell miljö (rekommenderas):**
    ```sh
    python3 -m venv venv
    source venv/bin/activate
    # För Windows: venv\Scripts\activate
    ```

3.  **Installera Python-paket:**
    ```sh
    pip install -r requirements.txt
    ```

## Konfiguration

1.  **Skapa en `.env`-fil**:
    - Skapa en fil med namnet `.env` i projektets rotmapp.
    - Lägg till `SHEET_ID` och `SHEET_GID` för ditt Google Sheet. Se till att kalkylbladet är publikt ("Alla med länken").
    ```
    SHEET_ID="din_sheet_id_här"
    SHEET_GID="din_sheet_gid_här"
    ```

2.  **`logo.png`**:
    - Lägg din logotyp i rotmappen. Den läggs till på varje sida i den slutgiltiga PDF:en.

## Användning

Kör skripten i ordning. Varje skript skapar nödvändiga mappar och loggar sitt arbete i `logs/`-mappen.

1.  **Hämta CSV-data från Google Sheet**
    ```sh
    python get_csv.py
    ```

2.  **Ladda ner kvitton**
    ```sh
    python download_from_drive.py
    ```

3.  **Bearbeta filer**
    ```sh
    python convert_to_jpg.py
    ```

4.  **Skapa PDF:er**
    ```sh
    python generate_PDF.py
    ```

De färdiga rapporterna finns nu i `final/`-mappen.
<br>
*För att köra hela processen med ett enda kommando:*
```sh
python get_csv.py && python download_from_drive.py && python convert_to_jpg.py && python generate_PDF.py
```