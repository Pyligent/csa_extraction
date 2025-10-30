import re
from typing import Literal


def detect_file_type_from_suffix(path: str) -> Literal["html", "pdf", "docx", "txt", "unknown"]:
    p = path.lower()
    if p.endswith(('.htm', '.html')):
        return "html"
    if p.endswith('.pdf'):
        return "pdf"
    if p.endswith(('.docx', '.doc')):
        return "docx"
    if p.endswith('.txt'):
        return "txt"
    return "unknown"


def clean_text_for_extraction(text: str) -> str:
    # Normalize whitespace and punctuation artifacts
    t = text.replace('\xa0', ' ').replace('\u00a0', ' ')
    # Collapse multiple spaces and stray non-breaking spaces
    t = re.sub(r'[\t\r]+', ' ', t)
    t = re.sub(r'\s*\n\s*', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = re.sub(r'\s{2,}', ' ', t)

    # Remove very common EDGAR boilerplate lines
    boilerplate_patterns = [
        r'^table of contents$',
        r'^index of exhibits$',
        r'^signature[s]?:?$',
        r'^page \d+ of \d+$',
    ]
    lines = []
    for line in t.split('\n'):
        stripped = line.strip().lower()
        if any(re.match(p, stripped) for p in boilerplate_patterns):
            continue
        lines.append(line)

    t = '\n'.join(lines)

    # Guard rails to reduce false party matches later
    # Replace sequences like “this Annex will prevail …” paragraphs with a shorter form
    t = re.sub(r'this\s+annex\s+will\s+prevail[\s\S]*?paragraph\s+13\s+will\s+prevail\.?', ' ', t, flags=re.IGNORECASE)
    return t.strip()


