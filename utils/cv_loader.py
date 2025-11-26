# utils/cv_loader.py

import os
import pdfplumber
import docx
import docx2txt


async def load_cv_text(path: str) -> str:
    """
    Load text content from a CV file.
    Supports PDF, DOCX, DOC, TXT.
    """

    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"CV file not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    # PDF
    if ext == ".pdf":
        try:
            text = ""
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
            return text.strip()
        except Exception:
            raise Exception("Failed to extract text from PDF")

    # DOCX
    if ext == ".docx":
        try:
            doc = docx.Document(path)
            return "\n".join([p.text for p in doc.paragraphs]).strip()
        except Exception:
            raise Exception("Failed to extract text from DOCX")

    # DOC
    if ext == ".doc":
        try:
            text = docx2txt.process(path)
            return text.strip()
        except Exception:
            raise Exception("Failed to extract text from DOC")

    # TXT
    if ext == ".txt":
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except:
            raise Exception("Failed to read TXT file")

    raise Exception(f"Unsupported CV file type: {ext}")
