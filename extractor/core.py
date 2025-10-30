from pathlib import Path

from .parsers.html import parse_html
from .parsers.pdf import parse_pdf
from .parsers.docx import parse_docx
from .parsers.txt import parse_txt


def extract_csa(file_path: str) -> dict:
    path = Path(file_path)
    content = path.read_bytes()
    if path.suffix.lower() in {".htm", ".html"}:
        return parse_html(content.decode("utf-8", errors="ignore"))
    elif path.suffix.lower() == ".pdf":
        return parse_pdf(content)
    elif path.suffix.lower() in {".docx", ".doc"}:
        return parse_docx(content)
    elif path.suffix.lower() == ".txt":
        return parse_txt(content.decode("utf-8", errors="ignore"))
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")
