# utils/cv_loader.py

import os
import pdfplumber
import docx
import docx2txt
import requests
import tempfile


async def load_cv_text(path: str) -> str:
    """
    Load text content from a CV file.
    Supports:
    - Local paths
    - HTTP / HTTPS / S3 URLs
    """

    if not path:
        raise FileNotFoundError("Empty CV path")

    # --- 1. Handle remote URLs ---
    if path.startswith("http://") or path.startswith("https://"):
        try:
            r = requests.get(path, timeout=10)
            r.raise_for_status()
        except Exception as e:
            raise FileNotFoundError(f"Could not download CV from URL: {e}")

        # Save to a temp file
        ext = os.path.splitext(path)[1].lower() or ".docx"
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp_file.write(r.content)
        tmp_file.close()
        path = tmp_file.name  # Use the downloaded local file

    # --- 2. Validate local file exists ---
    if not os.path.exists(path):
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
        except Exception:
            raise Exception("Failed to read TXT file")

    raise Exception(f"Unsupported CV file type: {ext}")
