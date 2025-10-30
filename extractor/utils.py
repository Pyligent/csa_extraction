import re
from typing import List, Optional


def find_anchor(text: str, anchors: List[str], case: bool = False) -> Optional[int]:
    flags = 0 if case else re.IGNORECASE
    for a in anchors:
        m = re.search(a, text, flags)
        if m:
            return m.start()
    return None


def extract_paragraph(text: str, start_idx: int, stop_anchors: List[str]) -> str:
    lines = [l.strip() for l in text[start_idx:].splitlines() if l.strip()]
    result: List[str] = []
    for line in lines:
        if any(re.search(a, line, re.I) for a in stop_anchors):
            break
        result.append(line)
    return " ".join(result).strip()


def parse_percentage(s: str) -> Optional[float]:
    cleaned = s.replace("%", "").replace(",", "").strip()
    m = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def normalize_regime(regime: str) -> str:
    mapping = {
        "S&P": "S&P",
        "Moody's First": "Moody's First Trigger",
        "Moody's Second": "Moody's Second Trigger",
        "Fitch": "Fitch",
        "Approved": "S&P Approved Ratings Downgrade",
        "Required": "S&P Required Ratings Downgrade",
        "First Trigger": "Moody's First Trigger",
        "Second Trigger": "Moody's Second Trigger",
    }
    for k, v in mapping.items():
        if k in regime:
            return v
    return regime.strip()
