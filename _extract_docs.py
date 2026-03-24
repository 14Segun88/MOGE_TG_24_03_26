import sys
import os
import subprocess

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package, "--quiet"])

try:
    import docx
except ImportError:
    install("python-docx")
    import docx

try:
    import pypdf
except ImportError:
    install("pypdf")
    import pypdf

from docx import Document
from pypdf import PdfReader

def extract_docx(path):
    try:
        doc = Document(path)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        return f"Error extracting DOCX {path}: {e}"

def extract_pdf(path):
    try:
        reader = PdfReader(path)
        return "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
    except Exception as e:
        return f"Error extracting PDF {path}: {e}"

dir_path = "/home/segun/Практика в машинном обучении"
files = [
    "АВТОМАТИЗИРОВАННАЯ СИСТЕМА ЭКСПЕРТИЗЫ ДОКУМЕНТАЦИИ.docx",
    "Сценарии_применения_Искусственного_интеллекта_в_экспертизе.docx",
    "Чек-листы (2) (1).pdf"
]

out_file = os.path.join(dir_path, "_parsed_docs.txt")
with open(out_file, "w", encoding="utf-8") as out:
    for f in files:
        path = os.path.join(dir_path, f)
        out.write(f"\n============== {f} ==============\n\n")
        if f.endswith('.docx'):
            out.write(extract_docx(path))
        elif f.endswith('.pdf'):
            out.write(extract_pdf(path))
        out.write("\n\n")
print(f"Done. Saved to {out_file}")
