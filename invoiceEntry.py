import os
import downloadFromDrive as DFD
from io import BytesIO
from PIL import Image
import subprocess

class InvoiceEntry:
    def __init__(self, entryAsList, invoiceTemplate):
        
        self.creationDate = entryAsList[0]
        self.invoiceDate = entryAsList[1]
        self.utskott = entryAsList[2]
        self.event = entryAsList[3]
        self.specification = entryAsList[4].replace("&", "\\&")
        self.name = entryAsList[5]
        self.sum = entryAsList[6]
        self.transactionType = entryAsList[7]
        self.verifications = entryAsList[8].split(", ")
        self.extra = (entryAsList[10]) + ((r"\vspace{.2cm}\newline" + entryAsList[9]) if (entryAsList[10] and entryAsList[9]) else entryAsList[9])
        self.additionalInfo = entryAsList[10]
        self.bankNumber = entryAsList[11]

        self.template = ""
        self.pictureTemplate = ""
        self.PDFTemplate = ""
        self.endTemplate = ""

        for section in invoiceTemplate.split("divider-"):
            # Only keep bankInfo if it is a private expense
            if (self.transactionType != "Privat utlägg") and ("bankInfo" in section):
                pass
            elif "exampleIMG.png" in section:
                self.pictureTemplate = section
            elif "examplePDF" in section:
                self.PDFTemplate = section
            elif r"\end{document}" in section:
                self.endTemplate = section
            else:
                self.template += section
        
        self.template = self.template % self.createDictionary()

        self.directory = "entries/" + self.__str__()
        try:
            os.mkdir(self.directory)
            os.mkdir(self.directory + "/filer")
            print(f"Directory '{self.directory}' created successfully.")
        except FileExistsError:
            print(f"Directory '{self.directory}' already exists.")
        except PermissionError:
            print(f"Permission denied: Unable to create '{self.directory}'.")
        except Exception as e:
            print(f"An error occurred: {e}")
        pass

    def __str__(self):
        return f"{self.name} {self.creationDate}".replace('.','-').replace(' ','_')
    
    def getAllInfo(self):
        return (f"{"Creation Date: " + self.creationDate + '\n' if self.creationDate else ""}"
                f"{"Invoice Date: " + self.invoiceDate + '\n' if self.invoiceDate else ""}"
                f"{"Utskott: " + self.utskott + '\n' if self.utskott else ""}"
                f"{"Event: " + self.event + '\n' if self.event else ""}"
                f"{"Specifications: " + self.specification + '\n' if self.specification else ""}"
                f"{"Name: " + self.name + '\n' if self.name else ""}"
                f"{"Sum: " + self.sum + '\n' if self.sum else ""}"
                f"{"Transaction Type: " + self.transactionType + '\n' if self.transactionType else ""}"
                f"{"Varification: " + self.verifications[0] + '\n' if self.verifications else ""}"
                f"{"Extra: " + self.extra + '\n' if self.extra else ""}"
                f"{"Bank Information: " + self.bankNumber + '\n' if self.bankNumber else ""}")
    
    def createDictionary(self):
        return {"invoiceDate" : self.invoiceDate if self.invoiceDate else "PLACEHOLDER",
                "utskott" : self.utskott if self.utskott else "PLACEHOLDER",
                "event" : self.event if self.event else "PLACEHOLDER",
                "specification" : self.specification if self.specification else "PLACEHOLDER",
                "name" : self.name if self.name else "PLACEHOLDER",
                "sum" : self.sum if self.sum else "PLACEHOLDER",
                "transactionType" : self.transactionType if self.transactionType else "PLACEHOLDER",
                "extra" : self.extra if self.extra else r"\phantom{test}",
                "bankInfo" : self.bankNumber if self.bankNumber else "PLACEHOLDER"}
    
    def printPictureLink(self):
        for link in self.verifications:
            print(link)
        print()

    def downloadVerifications(self, compression=30):
        for i in range(len(self.verifications)):
            self.verifications[i] = self.verifications[i].split("?id=")[-1]
            imagePath = self.directory + '/filer/' + self.verifications[i]
            response = DFD.download_file_from_google_drive(self.verifications[i], imagePath)

            if response.headers["Content-Type"].split("/")[0] == "image":
                img = Image.open(imagePath + "_original.jpg")
                img = img.convert('L')
                img.save(imagePath + ".jpg")

                buffer = BytesIO()
                img.save(buffer, "JPEG", quality=compression)

                with open(imagePath + ".jpg", "wb") as handle:
                    handle.write(buffer.getbuffer())
            else:
                self.verifications[i] += "PDFFILE"

    def createPDF(self):
        self.addPhotoToPDF()
        self.template += self.endTemplate

        # Create the .tex file
        with open(self.directory + '/' + self.__str__() + ".tex", 'w', encoding="UTF-8", newline='') as result:
                result.write(self.template)
        
        # Creates the PDF from the .tex
        cmd = ["pdflatex", "-interaction", "nonstopmode", "-output-directory", "finishedPDF/", 
                   "-aux-directory", "otherFiles/", self.directory + '/' + self.__str__() + ".tex"]
        proc = subprocess.Popen(cmd,shell=True)
        proc.communicate()

    def addPhotoToPDF(self):
        for verification in self.verifications:
            if "PDFFILE" in verification:
                self.template += self.PDFTemplate.replace("examplePDF", self.directory + "/filer/" + verification.replace("PDFFILE", ''))
            else:
                self.template += self.pictureTemplate.replace("exampleIMG.png", self.directory + "/filer/" + verification + ".jpg")
