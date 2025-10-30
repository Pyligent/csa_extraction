from io import BytesIO
from typing import Dict

from docx import Document


def parse_docx(docx_bytes: bytes) -> Dict:
    doc = Document(BytesIO(docx_bytes))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    from .html import parse_html

    return parse_html(text)
