import csv
import invoiceEntry as IE
import os

def main():    
    invoiceEntries = []
    content = ""
    with open("mall.tex", "r", encoding="UTF-8", newline='') as pdfmall:
        content = pdfmall.read()

    with open("Kvittomall.csv", "r", encoding='UTF-8', newline='') as invoiceTemplate:
        # Read the CSV and skip first row.
        entries = csv.reader(invoiceTemplate, dialect="excel")
        next(entries, None)

        # Loop over the entries given
        for entry in entries:
            if not os.path.exists("entries/"+ f"{entry[5]} {entry[0]}".replace('.','-').replace(' ','_')):
                invoiceEntries.append(IE.InvoiceEntry(entry, content))
    
    # Create PDF for all entries
    for invoiceEntry in invoiceEntries:
        #print(invoiceEntry.getAllInfo())
        #invoiceEntry.printPictureLink()
        invoiceEntry.downloadVerifications(30)
        invoiceEntry.createPDF()
        pass
