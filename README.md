# Automatisk generering av kvittomallar

Detta projekt automatiserar skapandet av kvittomallar. Det läser data från en CSV-fil, laddar ner kvitton från Google Drive och skapar en sammanställd PDF för varje utlägg.

## Arbetsflöde

Processen är uppdelad i tre steg. Utdata från ett steg blir indata för nästa.
1.  **Ladda ner kvitton**: Filer från Google Drive sparas i mappen `downloads/`.
2.  **Bearbeta filer**: Bilder konverteras till JPG och alla filer standardiseras och sparas i mappen `processed/`.
3.  **Skapa PDF:er**: Färdiga kvittomallar skapas från filerna i `processed/` och sparas i mappen `final/`.

## Förutsättningar

- **Python 3.7+**
- **Poppler**: Krävs för PDF-hantering.
  - **macOS**: `brew install poppler`
  - **Ubuntu/Debian**: `sudo apt-get install poppler-utils`
  - **Windows**: Ladda ner Poppler och lägg till dess `bin/`-mapp i din PATH.

## Installation

1.  **Klona projektet:**
    ```sh
    git clone <din-repo-url>
    cd <projektmapp>
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

1.  **`responses.csv`**:
    - Lägg din CSV-fil i projektets rotmapp.
    - Filen måste innehålla kolumner som `Tidstämpel`, `Namn`, `Summa`, `Utskott`, `Arrangemang` och `Ladda upp kvittot` (en kommaseparerad lista med Google Drive-länkar).

2.  **`logo.png`**:
    - Lägg din logotyp i rotmappen. Den läggs till på varje sida i den slutgiltiga PDF:en.

## Användning

Kör skripten i ordning. Varje skript skapar nödvändiga mappar och loggar sitt arbete i `logs/`-mappen.

1.  **Ladda ner kvitton**
    ```sh
    python download_from_drive.py
    ```

2.  **Bearbeta filer**
    ```sh
    python convert_to_jpg.py
    ```

3.  **Skapa PDF:er**
    ```sh
    python generate_PDF.py
    ```

De färdiga rapporterna finns nu i `final/`-mappen.
<br>
*För att köra hela processen med ett enda kommando:*
```sh
python download_from_drive.py && python convert_to_jpg.py && python generate_PDF.py
```