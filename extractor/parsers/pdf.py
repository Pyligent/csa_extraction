import io
from typing import Dict

import pdfplumber


def parse_pdf(pdf_bytes: bytes) -> Dict:
    # pdfplumber expects a filepath or file-like object
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    from .html import parse_html

    return parse_html(text)
