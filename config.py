"""
Centralized configuration for the Kvittomall application.

This file contains constants for CSV column names and other configuration
values that are shared across different scripts in the project.
This approach avoids "magic strings" and makes the application easier to
maintain and configure.
"""

# --- Core CSV Column Names ---
# These are the exact names of the columns in the Google Sheet/CSV file.
TIMESTAMP_COLUMN = "Tidstämpel"
NAME_COLUMN = "Namn"
RECEIPT_LINKS_COLUMN = "Ladda upp kvittot"
TRANSACTION_TYPE_COLUMN = "Transaktionstyp"

# --- PDF Generation Configuration ---
# Column name for the "Summa" field, used for special formatting (adding "kr").
SUM_COLUMN = "Summa"

# --- PDF Content Strings ---
# Used for generating the text content within the PDFs.
PDF_TITLE_PREFIX = "Kvittomall - "
PDF_ATTACHMENT_PAGE_TITLE = f"{PDF_TITLE_PREFIX}Bild på kvittot"
CURRENCY_SUFFIX = " kr"

# Defines the structure of the main text page in the generated PDF.
# Each inner list is a section on the page.
# Each tuple within a section is a (PDF Label, CSV Header Name).
PDF_SECTIONS = [
    [
        ("Datum:", "Datum på kvittot"),
        ("Namn:", NAME_COLUMN),
        ("Summa:", SUM_COLUMN),
        ("Kontonummer:", "Kontonummer"),
    ],
    [
        ("Utskott:", "Utskott"),
        ("Arrangemang:", "Arrangemang"),
        ("Specificering:", "Specificering"),
    ],
    [
        ("Övrigt:", "Övrigt"),
        ("Extra info:", "Extra info")
    ]
]
