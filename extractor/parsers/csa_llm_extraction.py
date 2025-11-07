# csa_extractor.py
import json
import asyncio
import re
import html
from typing import List, Dict, Any, Optional, Literal, Tuple
import inspect
from pydantic import BaseModel, Field, validator
from bs4 import BeautifulSoup
import io
try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv, find_dotenv

# Ensure .env is discovered from project root when running via CLI/tests
load_dotenv(find_dotenv(), override=True)

# ========================================
# 1. HTML → CLEAN TEXT PREPROCESSOR
# ========================================
def html_to_text(html_content: str) -> str:
    """Convert HTML to clean text, preserving table structure as plain rows for LLM."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove unwanted tags (keep tables)
    for tag in soup(["script", "style", "header", "footer", "nav", "aside"]):
        tag.decompose()

    # Build a plain-text representation of tables to aid haircuts parsing
    table_text_blocks = []
    for tbl in soup.find_all("table"):
        try:
            headers = [th.get_text(" ", strip=True) for th in tbl.find_all("th")]
            rows = []
            for tr in tbl.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                block = "TABLE:\n" + "\n".join(rows)
                table_text_blocks.append(block)
        except Exception:
            continue

    # Extract remaining text
    plain_text = soup.get_text(separator="\n")
    lines = (line.strip() for line in plain_text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    plain_text = "\n".join(chunk for chunk in chunks if chunk)
    plain_text = re.sub(r"\n{3,}", "\n\n", plain_text)
    plain_text = re.sub(r" +", " ", plain_text)
    plain_text = html.unescape(plain_text)

    # Append table blocks at the end to keep anchors + tabular data together
    if table_text_blocks:
        plain_text += "\n\n" + "\n\n".join(table_text_blocks)
    return plain_text.strip()


def extract_13c2_block_text(html_content: str) -> Optional[str]:
    """Extract only the Paragraph 13(c)(ii) 'Eligible Collateral (VM)' block as text.
    Returns None if not found."""
    soup = BeautifulSoup(html_content, "html.parser")
    # Find a node that signals the start
    start_node = None
    for node in soup.find_all(text=True):
        t = (node.strip() or "").lower()
        if not t:
            continue
        if "13(c)(ii)" in t or "paragraph 13(c)(ii)" in t or "eligible collateral (vm)" in t:
            start_node = node.parent if hasattr(node, 'parent') else None
            break
    if not start_node:
        return None
    # Collect following siblings' text until next section marker
    block_text_lines = []
    current = start_node
    stop_patterns = re.compile(r"13\(c\)\(iii\)|paragraph\s*13\(c\)\(iii\)|concentration\s+limits|caps?\s*windows?", re.IGNORECASE)
    # Traverse within same parent subtree up to reasonable depth
    for _ in range(0, 400):
        if current is None:
            break
        txt = current.get_text("\n", strip=True)
        if txt and stop_patterns.search(txt):
            break
        if txt:
            block_text_lines.append(txt)
        current = current.find_next_sibling()
    if not block_text_lines:
        return None
    # Join and lightly normalize
    block_text = "\n".join(block_text_lines)
    block_text = re.sub(r"\n{3,}", "\n\n", block_text)
    block_text = re.sub(r" +", " ", block_text)
    return block_text.strip()


def extract_13c2_table_text(html_content: str) -> Optional[str]:
    """Build a compact, header-first tabular text for 13(c)(ii) to improve LLM recall."""
    soup = BeautifulSoup(html_content, "html.parser")
    # Locate starting node
    start_node = None
    for node in soup.find_all(text=True):
        t = (node.strip() or "").lower()
        if not t:
            continue
        if "13(c)(ii)" in t or "paragraph 13(c)(ii)" in t or "eligible collateral (vm)" in t:
            start_node = node.parent if hasattr(node, 'parent') else None
            break
    if not start_node:
        return None
    stop_patterns = re.compile(r"13\(c\)\(iii\)|paragraph\s*13\(c\)\(iii\)|concentration\s+limits|caps?\s*windows?", re.IGNORECASE)
    # Gather any tables within this block
    collected_tables = []
    current = start_node
    for _ in range(0, 200):
        if current is None:
            break
        txt = current.get_text("\n", strip=True)
        if txt and stop_patterns.search(txt):
            break
        # If current contains a table, serialize it
        for tbl in current.find_all("table"):
            try:
                if pd is not None:
                    s = io.StringIO(tbl.prettify())
                    dfs = pd.read_html(s, header=0)
                    for df in dfs:
                        if df is None or df.empty:
                            continue
                        # convert to pipe-separated header-first text
                        lines = []
                        try:
                            headers = [str(h) for h in df.columns]
                            lines.append(" | ".join(headers))
                        except Exception:
                            pass
                        for _, row in df.iterrows():
                            try:
                                cells = [str(v) for v in row.tolist()]
                                lines.append(" | ".join(cells))
                            except Exception:
                                continue
                        if lines:
                            collected_tables.append("\n".join(lines))
                else:
                    # bs4-only serialization
                    lines = []
                    headers = [th.get_text(" ", strip=True) for th in tbl.find_all("th")]
                    if headers:
                        lines.append(" | ".join(headers))
                    for tr in tbl.find_all("tr"):
                        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                        if cells:
                            lines.append(" | ".join(cells))
                    if lines:
                        collected_tables.append("\n".join(lines))
            except Exception:
                continue
        current = current.find_next_sibling()
    if not collected_tables:
        return None
    return ("\n\n".join(collected_tables)).strip()


def extract_13c2_rows_text(html_content: str) -> Optional[str]:
    """Produce simple, line-oriented rows from 13(c)(ii) to aid LLM parsing.
    Format:
    Regimes: sp|m1|m2|fitch
    Asset: <asset>
    Maturity: <maturity line>
    Values: 100|99|98|97
    """
    soup = BeautifulSoup(html_content, "html.parser")
    # Find start of 13(c)(ii)
    start_node = None
    for node in soup.find_all(text=True):
        t = (node.strip() or "").lower()
        if not t:
            continue
        if "13(c)(ii)" in t or "eligible collateral (vm)" in t or "paragraph 13(c)(ii)" in t:
            start_node = node.parent if hasattr(node, 'parent') else None
            break
    if not start_node:
        return None
    stop_patterns = re.compile(r"13\(c\)\(iii\)|paragraph\s*13\(c\)\(iii\)|concentration\s+limits|caps?\s*windows?", re.IGNORECASE)
    lines = []
    current = start_node
    for _ in range(0, 400):
        if current is None:
            break
        txt = current.get_text(" ", strip=True)
        if txt and stop_patterns.search(txt):
            break
        if txt:
            lines.append(txt)
        current = current.find_next_sibling()
    if not lines:
        return None
    # Regime header guess from first 10 lines
    regimes: list[str] = []
    for ln in lines[:10]:
        parts = re.split(r"\s{2,}|\|", ln)
        if sum(1 for p in parts if re.search(r"S&P|Moody.?s|Fitch", p, re.IGNORECASE)) >= 2:
            for p in parts:
                if re.search(r"S&P|Standard\s*&\s*Poor", p, re.IGNORECASE):
                    regimes.append("sp")
                elif re.search(r"Moody.?s\s*First|\(1st\)", p, re.IGNORECASE):
                    regimes.append("m1")
                elif re.search(r"Moody.?s\s*Second|\(2nd\)", p, re.IGNORECASE):
                    regimes.append("m2")
                elif re.search(r"Fitch", p, re.IGNORECASE):
                    regimes.append("fitch")
            break
    out_lines = []
    if regimes:
        out_lines.append("Regimes: " + "|".join(regimes))
    current_asset = None
    def is_asset(s: str) -> bool:
        return bool(re.search(r"Cash|Sovereign|Government|Municipal|GSE|Obligations|Treasury|Bonds|Notes", s, re.IGNORECASE))
    for ln in lines:
        if is_asset(ln):
            current_asset = ln.rstrip(":")
            continue
        if not current_asset:
            continue
        if re.search(r"\d", ln):
            nums = [m.group(1) for m in re.finditer(r"([\d]+(?:\.[\d]+)?)\s*%?", ln)]
            if nums:
                out_lines.append("Asset: " + current_asset)
                out_lines.append("Maturity: " + ln)
                out_lines.append("Values: " + "|".join(nums))
    return "\n".join(out_lines) if out_lines else None


# ==========================
# SELF-HEALING FALLBACKS
# ==========================
def _fallback_parse_haircuts(html_content: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if pd is None:
        return rows
    soup = BeautifulSoup(html_content, "html.parser")
    for table in soup.find_all("table"):
        try:
            s = io.StringIO(table.prettify())
            dfs = pd.read_html(s, header=0)
        except Exception:
            continue
        for df in dfs:
            if df is None or df.empty:
                continue
            # Try to derive regimes from columns, else from an in-table header row
            regimes: List[Optional[str]] = []
            derived_from_row = False
            try:
                tmp: List[Optional[str]] = []
                for c in df.columns:
                    lbl = str(c)
                    if re.search(r"S&P|Standard\s*&\s*Poor", lbl, re.IGNORECASE):
                        tmp.append("sp")
                    elif re.search(r"Moody.?s\s*First|\(1st\)", lbl, re.IGNORECASE):
                        tmp.append("m1")
                    elif re.search(r"Moody.?s\s*Second|\(2nd\)", lbl, re.IGNORECASE):
                        tmp.append("m2")
                    elif re.search(r"Fitch", lbl, re.IGNORECASE):
                        tmp.append("fitch")
                    else:
                        tmp.append(None)
                regimes = tmp
            except Exception:
                regimes = [None] * len(df.columns)
            # If not enough regime signals in columns, scan first 5 rows for regime labels
            if sum(1 for r in regimes if r) < 2:
                for ridx in range(min(5, len(df))):
                    row_vals = [str(x) for x in df.iloc[ridx].tolist()]
                    signals = 0
                    cand: List[Optional[str]] = []
                    for v in row_vals:
                        if re.search(r"S&P|Standard\s*&\s*Poor", v, re.IGNORECASE):
                            cand.append("sp"); signals += 1
                        elif re.search(r"Moody.?s\s*First|\(1st\)", v, re.IGNORECASE):
                            cand.append("m1"); signals += 1
                        elif re.search(r"Moody.?s\s*Second|\(2nd\)", v, re.IGNORECASE):
                            cand.append("m2"); signals += 1
                        elif re.search(r"Fitch", v, re.IGNORECASE):
                            cand.append("fitch"); signals += 1
                        else:
                            cand.append(None)
                    if signals >= 2:
                        regimes = cand
                        # Drop the header row from data
                        df = df.drop(df.index[ridx])
                        derived_from_row = True
                        break
            for _, r in df.iterrows():
                try:
                    asset = str(r.iloc[0]).strip()
                except Exception:
                    continue
                maturity = None
                if len(r) > 1 and not isinstance(r.iloc[1], (int, float)):
                    maturity = str(r.iloc[1]).strip()
                start_col = 2 if maturity else 1
                for i in range(start_col, len(r)):
                    m = re.search(r"([\d]+(?:\.[\d]+)?)\s*%?", str(r.iloc[i]))
                    if not m:
                        continue
                    try:
                        pct = float(m.group(1))
                    except Exception:
                        continue
                    regime = regimes[i] if i < len(regimes) else None
                    rows.append({
                        "asset_type": asset,
                        "maturity_bucket": maturity or "N/A",
                        "regime": regime,
                        "valuation_percentage": pct,
                    })
    return rows


def _fallback_parse_rounding(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    def parse_rounding(block: str) -> Optional[Dict[str, Any]]:
        m = re.search(r"rounded\s+(up|down)[\s\S]{0,40}?nearest[\s\S]{0,80}?([A-Z]{3})\s*([\d,]+)", block, re.IGNORECASE)
        if m:
            return {"direction": m.group(1).upper(), "amount": float(m.group(3).replace(",", "")), "currency": m.group(2).upper()}
        m2 = re.search(r"rounded\s+(up|down)[^\w]+([\d,]+)\s+([A-Z]{3})", block, re.IGNORECASE)
        if m2:
            return {"direction": m2.group(1).upper(), "amount": float(m2.group(2).replace(",", "")), "currency": m2.group(3).upper()}
        return None

    d_block = re.search(r"Delivery\s+Amount[\s\S]{0,240}?rounded[\s\S]{0,240}?nearest[\s\S]{0,120}?\d", text, re.IGNORECASE)
    r_block = re.search(r"Return\s+Amount[\s\S]{0,240}?rounded[\s\S]{0,240}?nearest[\s\S]{0,120}?\d", text, re.IGNORECASE)
    d = parse_rounding(d_block.group(0)) if d_block else None
    r = parse_rounding(r_block.group(0)) if r_block else None
    return d, r


def _fallback_mta_currency(text: str) -> Optional[str]:
    m1 = re.search(r"Minimum\s+Transfer\s+Amount[^\w]*([A-Z]{3})\s*[\$\s]*[\d,]+", text, re.IGNORECASE)
    if m1:
        return m1.group(1).upper()
    if re.search(r"\bMTA[^\w]*\$\s*[\d,]+", text, re.IGNORECASE):
        return "USD"
    if re.search(r"United\s+States\s+Dollars|US\s*Dollars|U\.?S\.?\s*Dollars", text, re.IGNORECASE):
        return "USD"
    return None


def _fallback_parse_haircuts_from_rows(html_content: str) -> List[Dict[str, Any]]:
    txt = extract_13c2_rows_text(html_content)
    rows: List[Dict[str, Any]] = []
    if not txt:
        return rows
    regimes: List[str] = []
    for line in txt.splitlines():
        if line.startswith("Regimes:"):
            regimes = [p.strip() for p in line.split(":", 1)[1].split("|") if p.strip()]
            break
    asset = None
    maturity = None
    values: List[str] = []
    def flush():
        nonlocal asset, maturity, values
        if asset and maturity and values and regimes:
            for i, v in enumerate(values):
                if i >= len(regimes):
                    break
                m = re.match(r"[\d]+(?:\.[\d]+)?", v.strip())
                if not m:
                    continue
                rows.append({
                    "asset_type": asset,
                    "maturity_bucket": maturity,
                    "regime": regimes[i] if regimes[i] in ("sp","m1","m2","fitch") else None,
                    "valuation_percentage": float(m.group(0))
                })
        asset = None
        maturity = None
        values = []
    for line in txt.splitlines():
        if line.startswith("Asset:"):
            flush()
            asset = line.split(":", 1)[1].strip()
        elif line.startswith("Maturity:"):
            maturity = line.split(":", 1)[1].strip()
        elif line.startswith("Values:"):
            values = [p.strip() for p in line.split(":", 1)[1].split("|")]
    flush()
    # Filter out rows without regime
    rows = [r for r in rows if r.get("regime") in ("sp","m1","m2","fitch")]
    return rows

# ========================================
# 2. PYDANTIC SCHEMA (A/B/C/D)
# ========================================
class RoundingConfig(BaseModel):
    direction: Literal["NEAREST", "UP", "DOWN"]
    amount: float
    currency: str

class HaircutRow(BaseModel):
    asset_type: str
    maturity_bucket: str
    regime: Literal["sp", "m1", "m2", "fitch"]
    valuation_percentage: float

class CapsWindows(BaseModel):
    cash_cap_pct_of_U: Optional[float] = None
    issuer_cap: Optional[str] = None
    class_cap: Optional[str] = None
    currency_cap: Optional[str] = None
    global_cap: Optional[str] = None

class CSAExtraction(BaseModel):
    # A. Document & Parties
    csa: dict = Field(default_factory=dict)
    parties: dict = Field(default_factory=dict)

    @validator("csa")
    def check_csa(cls, v):
        # Ensure meta exists and has expected keys with permissive defaults
        meta = v.get("meta") or {}
        gov = meta.get("governing_law")
        if isinstance(gov, str) and gov not in ["NY", "English", "Japanese", "Ontario", "Singapore", "Other"]:
            gov = None
        # Default missing keys to None
        meta.setdefault("governing_law", gov if gov is not None else None)
        meta.setdefault("agreement_date", meta.get("agreement_date", None))
        ow = meta.get("one_way")
        if ow not in [True, False, None]:
            ow = None
        meta.setdefault("one_way", ow if ow is not None else None)
        v["meta"] = meta
        return v

    @validator("parties")
    def check_parties(cls, v):
        pa = v.get("party_A") or {}
        pb = v.get("party_B") or {}
        # Ensure keys exist
        pa.setdefault("name", pa.get("name", None))
        pb.setdefault("name", pb.get("name", None))
        ra = pa.get("role")
        rb = pb.get("role")
        # Normalize common role phrasings
        def norm_role(x):
            if x is None:
                return None
            if not isinstance(x, str):
                return None
            xl = x.strip().lower()
            if xl.startswith("secured"):
                return "Secured"
            if xl.startswith("pledgor"):
                return "Pledgor"
            if xl.startswith("both"):
                return "Both"
            return None
        if ra not in ["Pledgor", "Secured", "Both", None]:
            ra = norm_role(ra)
        if rb not in ["Pledgor", "Secured", "Both", None]:
            rb = norm_role(rb)
        if ra not in ["Pledgor", "Secured", "Both", None]:
            ra = None
        if rb not in ["Pledgor", "Secured", "Both", None]:
            rb = None
        pa.setdefault("role", ra if ra is not None else None)
        pb.setdefault("role", rb if rb is not None else None)
        v["party_A"] = pa
        v["party_B"] = pb
        return v

    # B. Core VM Mechanics
    terms: dict = Field(default_factory=dict)

    @validator("terms")
    def check_terms(cls, v):
        # MTA
        if "mta" in v:
            amt = v["mta"].get("amount")
            cur = v["mta"].get("currency")
            if not ((amt is None) or isinstance(amt, (int, float))):
                v["mta"]["amount"] = None
            if isinstance(cur, str):
                if cur.strip() in ["$", "US$"]:
                    v["mta"]["currency"] = "USD"
                elif len(cur) != 3:
                    v["mta"]["currency"] = None
            else:
                v["mta"]["currency"] = None
        else:
            v["mta"] = {"amount": None, "currency": None}

        # Rounding
        if "rounding" in v:
            for key in ["delivery", "return"]:
                entry = v["rounding"].get(key)
                if entry is None:
                    continue
                if isinstance(entry, dict):
                    # Validate expected shape
                    RoundingConfig(**entry)
                else:
                    # Coerce non-dict (e.g., string) to None to avoid validation errors
                    v["rounding"][key] = None

        # Return timing
        if "return_timing" in v:
            days = v["return_timing"].get("days")
            if not ((days is None) or isinstance(days, int)):
                v["return_timing"]["days"] = None
        else:
            v["return_timing"] = {"days": None}

        return v

    # C. Currencies & FX
    @validator("terms")
    def check_currencies(cls, v):
        if "base_currency" in v:
            bc = v.get("base_currency")
            if not ((bc is None) or (isinstance(bc, str) and len(bc) == 3)):
                v["base_currency"] = None
        if "eligible_currencies" in v:
            lst = v.get("eligible_currencies")
            if lst is None:
                v["eligible_currencies"] = []
            else:
                if not isinstance(lst, list):
                    v["eligible_currencies"] = []
                else:
                    v["eligible_currencies"] = [c for c in lst if isinstance(c, str) and len(c) == 3]
        else:
            v["eligible_currencies"] = []
        if "eligible_currency_includes_base" in v:
            if v["eligible_currency_includes_base"] not in [True, False, None]:
                v["eligible_currency_includes_base"] = None
        if "fx_haircut_pct" in v:
            if not (isinstance(v["fx_haircut_pct"], (int, float)) or v["fx_haircut_pct"] is None):
                v["fx_haircut_pct"] = None
        return v

    # D. Eligible Collateral & Haircuts
    eligibility: dict = Field(default_factory=dict)
    haircuts: dict = Field(default_factory=dict)
    caps_windows: Optional[CapsWindows] = None

    @validator("eligibility")
    def check_eligibility(cls, v):
        if "covered_transactions" in v:
            if v["covered_transactions"] is None:
                v["covered_transactions"] = []
            else:
                assert isinstance(v["covered_transactions"], list)
        else:
            v["covered_transactions"] = []
        if "spot_fx_carveout" in v:
            assert v["spot_fx_carveout"] in [True, False, None]
        return v

    @validator("haircuts")
    def check_haircuts(cls, v):
        if "matrix" in v:
            if v["matrix"] is None:
                v["matrix"] = []
            else:
                assert isinstance(v["matrix"], list)
                cleaned = []
                for row in v["matrix"]:
                    if not isinstance(row, dict):
                        continue
                    required = {"asset_type", "maturity_bucket", "regime", "valuation_percentage"}
                    if not required.issubset(row.keys()):
                        continue
                    try:
                        HaircutRow(**row)
                    except Exception:
                        continue
                    else:
                        cleaned.append(row)
                v["matrix"] = cleaned
        else:
            v["matrix"] = []
        return v

    @validator("caps_windows")
    def check_caps(cls, v):
        if v:
            CapsWindows(**v.dict(exclude_none=True))
        return v

# ========================================
# 3. FIELD LIST & MAX TOKENS
# ========================================
FIELDS = [
    "csa.meta.governing_law", "csa.meta.agreement_date", "csa.meta.one_way",
    "parties.party_A.name", "parties.party_B.name", "parties.party_A.role", "parties.party_B.role",
    "terms.valuation_agent", "terms.notification_time", "terms.valuation_date", "terms.valuation_time",
    "terms.regular_settlement_day", "terms.delivery_amount", "terms.return_amount",
    "terms.mta.amount", "terms.mta.currency", "terms.rounding.delivery", "terms.rounding.return",
    "terms.dispute.notice_cutoff", "terms.dispute.resolution_timing", "terms.return_timing.days",
    "terms.base_currency", "terms.eligible_currencies", "terms.eligible_currency_includes_base", "terms.fx_haircut_pct",
    "eligibility.covered_transactions", "eligibility.spot_fx_carveout", "eligibility.ratings_condition",
    "eligibility.issuer_constraints", "csa.regime.default", "haircuts.matrix",
    "caps_windows.cash_cap_pct_of_U", "caps_windows.issuer_cap", "caps_windows.class_cap",
    "caps_windows.currency_cap", "caps_windows.global_cap"
]

MAX_TOKENS = {
    "csa.meta.governing_law": 100, "csa.meta.agreement_date": 100, "csa.meta.one_way": 50,
    "parties.party_A.name": 200, "parties.party_B.name": 200, "parties.party_A.role": 50, "parties.party_B.role": 50,
    "terms.valuation_agent": 100, "terms.notification_time": 200, "terms.valuation_date": 300,
    "terms.valuation_time": 200, "terms.regular_settlement_day": 200,
    "terms.mta.amount": 50, "terms.mta.currency": 50,
    "terms.rounding.delivery": 300, "terms.rounding.return": 300,
    "terms.dispute.notice_cutoff": 200, "terms.dispute.resolution_timing": 300,
    "terms.return_timing.days": 50, "terms.base_currency": 50,
    "terms.eligible_currencies": 200, "terms.eligible_currency_includes_base": 50,
    "terms.fx_haircut_pct": 50, "eligibility.covered_transactions": 300,
    "eligibility.spot_fx_carveout": 50, "eligibility.ratings_condition": 1000,
    "eligibility.issuer_constraints": 1000, "csa.regime.default": 100,
    "caps_windows.cash_cap_pct_of_U": 100, "caps_windows.issuer_cap": 200,
    "caps_windows.class_cap": 200, "caps_windows.currency_cap": 200, "caps_windows.global_cap": 200,
    "terms.delivery_amount": 2000, "terms.return_amount": 2000, "haircuts.matrix": 4000,
}

# ========================================
# 4. PROMPTS (generic, no examples)
# ========================================
PROMPT_TEMPLATES = {
    # A. Document & Parties
    "csa.meta.governing_law": (
        "Extract ONLY the primary governing law.\n"
        "Look for: 'governed by', 'construed in accordance with', 'New York law', 'English law', 'Japanese law', 'laws of Ontario', 'laws of Singapore'.\n"
        "Return ONE of: 'NY', 'English', 'Japanese', 'Ontario', 'Singapore', 'Other'.\n"
        "If mixed → first mentioned. If not found → null.\n"
        "Return ONLY: {\"csa.meta.governing_law\": value}"
    ),
    "csa.meta.agreement_date": (
        "Extract ONLY the execution date.\n"
        "Look for: 'dated as of', 'this Annex is made on', 'executed on', title block.\n"
        "Return exact string. If not found → null.\n"
        "Return ONLY: {\"csa.meta.agreement_date\": value}"
    ),
    "csa.meta.one_way": (
        "Extract ONLY if CSA is one-way.\n"
        "Look for: 'Pledgor' defined for only one party, 'only Party A will deliver', 'Unilateral Form', 'one-way'.\n"
        "Return true if one-way, false if bilateral. If not explicit → null.\n"
        "Return ONLY: {\"csa.meta.one_way\": value}"
    ),
    "parties.party_A.name": (
        "Extract ONLY Party A's full legal name.\n"
        "Best source: title block, signature page, or first mention after 'between'. Do NOT use filename.\n"
        "Return string or null.\n"
        "Return ONLY: {\"parties.party_A.name\": value}"
    ),
    "parties.party_B.name": (
        "Extract ONLY Party B's full legal name.\n"
        "Same rules as Party A.\n"
        "Return ONLY: {\"parties.party_B.name\": value}"
    ),
    "parties.party_A.role": (
        "Extract ONLY Party A role.\n"
        "Look for: 'Pledgor', 'Secured Party', 'both'. If not explicit → null.\n"
        "Return ONLY: {\"parties.party_A.role\": value}"
    ),
    "parties.party_B.role": (
        "Extract ONLY Party B role.\n"
        "Same as Party A.\n"
        "Return ONLY: {\"parties.party_B.role\": value}"
    ),

    # B. Core VM Mechanics
    "terms.valuation_agent": (
        "Extract ONLY from Para 13(d)(i) or 'Valuation Agent'.\n"
        "Look for: 'Valuation Agent means [party]'.\n"
        "Return 'Party A', 'Party B', or 'both parties'; or the exact clause if no short label fits. If not found → null.\n"
        "Return ONLY: {\"terms.valuation_agent\": value}"
    ),
    "terms.notification_time": (
        "Extract ONLY from Para 13(d)(iv) or 'Notification Time'.\n"
        "Look for: 'Notification Time means [time]'. Return exact string. If not found → null.\n"
        "Return ONLY: {\"terms.notification_time\": value}"
    ),
    "terms.valuation_date": (
        "Extract ONLY from Para 13(d)(ii).\n"
        "Look for: 'Valuation Date means [rule]'. Return full text. If not found → null.\n"
        "Return ONLY: {\"terms.valuation_date\": value}"
    ),
    "terms.valuation_time": (
        "Extract ONLY from Para 13(d)(iii).\n"
        "Look for: 'Valuation Time means [time]'. Return exact string. If not found → null.\n"
        "Return ONLY: {\"terms.valuation_time\": value}"
    ),
    "terms.regular_settlement_day": (
        "Extract ONLY from Para 12 or 'Regular Settlement Day'.\n"
        "Look for: 'Regular Settlement Day means [day]'. Return exact text (e.g., 'Local Business Day'). If not found → null.\n"
        "Return ONLY: {\"terms.regular_settlement_day\": value}"
    ),
    "terms.delivery_amount": (
        "Extract ONLY from Para 13(b)(i)(A).\n"
        "Look for: 'Delivery Amount' definition. Return full custom text (include multi-regime if present). Do NOT simplify. If not found → null.\n"
        "Return ONLY: {\"terms.delivery_amount\": value}"
    ),
    "terms.return_amount": (
        "Extract ONLY from Para 13(b)(i)(B).\n"
        "Look for: 'Return Amount' definition. Return full custom text (include multi-regime if present). Do NOT simplify. If not found → null.\n"
        "Return ONLY: {\"terms.return_amount\": value}"
    ),
    "terms.mta.amount": (
        "Extract ONLY from Para 13(b)(iv) or 'Minimum Transfer Amount'.\n"
        "Look for: 'Minimum Transfer Amount: [number]'. Return number only. If not found → null.\n"
        "Return ONLY: {\"terms.mta.amount\": value}"
    ),
    "terms.mta.currency": (
        "Extract ONLY the currency of the Minimum Transfer Amount.\n"
        "Look for: 'USD 250,000', 'EUR 200,000', or 'in United States Dollars'.\n"
        "Return ONLY: {\"terms.mta.currency\": 'USD'|'EUR'|'GBP'|'JPY'|... or null}"
    ),
    "terms.rounding.delivery": (
        "Extract ONLY the rounding rule for Delivery Amount.\n"
        "Look for: 'rounded down to the nearest USD 10,000' or 'rounded down to nearest 10,000 USD'.\n"
        "Return ONLY: {\"terms.rounding.delivery\": {\"direction\": 'DOWN'|'UP', \"amount\": number, \"currency\": ISO} or null}"
    ),
    "terms.rounding.return": (
        "Extract ONLY from Para 13(b)(iv). Same as delivery.\n"
        "Return ONLY: {\"terms.rounding.return\": value}"
    ),
    "terms.dispute.notice_cutoff": (
        "Extract ONLY from Para 13(f).\n"
        "Look for: 'by the Notification Time' or 'close of business on the Local Business Day following'. Return exact string. If not found → null.\n"
        "Return ONLY: {\"terms.dispute.notice_cutoff\": value}"
    ),
    "terms.dispute.resolution_timing": (
        "Extract ONLY from Para 13(f).\n"
        "Look for: 'Resolution Time means [time]'. Return exact string. If not found → null.\n"
        "Return ONLY: {\"terms.dispute.resolution_timing\": value}"
    ),
    "terms.return_timing.days": (
        "Extract ONLY from Para 13(e).\n"
        "Look for: 'Local Business Day following' → 1, 'two' → 2. Return number; if not found → null.\n"
        "Return ONLY: {\"terms.return_timing.days\": value}"
    ),

    # C. Currencies & FX
    "terms.base_currency": (
        "Extract ONLY from Para 13(a)(i).\n"
        "Look for: 'Base Currency: [currency]' or 'United States Dollars'. Return ISO 4217 code; if not found → null.\n"
        "Return ONLY: {\"terms.base_currency\": value}"
    ),
    "terms.eligible_currencies": (
        "Extract ONLY from Para 13(a)(ii).\n"
        "Look for: 'Eligible Currency: [list]'. Return array of ISO codes. If 'as agreed' → []; if not found → [].\n"
        "Return ONLY: {\"terms.eligible_currencies\": value}"
    ),
    "terms.eligible_currency_includes_base": (
        "Compare base_currency and eligible_currencies. Return true only if base is in list; if not explicit → null.\n"
        "Return ONLY: {\"terms.eligible_currency_includes_base\": value}"
    ),
    "terms.fx_haircut_pct": (
        "Extract ONLY from Para 13(c)(v)(B).\n"
        "Look for: 'FX Haircut Percentage: 8%'. Return number; if not found → null.\n"
        "Return ONLY: {\"terms.fx_haircut_pct\": value}"
    ),

    # D. Eligible Collateral & Haircuts
    "eligibility.covered_transactions": (
        "Extract ONLY from Para 13(b)(ii).\n"
        "Look for: 'Covered Transactions: [text]'. Return ['All Transactions'] or exact list. If 'as set forth' → []; if not found → [].\n"
        "Return ONLY: {\"eligibility.covered_transactions\": value}"
    ),
    "eligibility.spot_fx_carveout": (
        "Extract ONLY from Para 13(b)(ii).\n"
        "Look for: 'except Spot FX'. Return true if excluded; if not found → null.\n"
        "Return ONLY: {\"eligibility.spot_fx_carveout\": value}"
    ),
    "eligibility.ratings_condition": (
        "Extract ONLY from Para 13(c)(iv) or table header.\n"
        "Look for: 'rated at least', 'NRSRO'. Return verbatim string; if not found → null.\n"
        "Return ONLY: {\"eligibility.ratings_condition\": value}"
    ),
    "eligibility.issuer_constraints": (
        "Extract ONLY from Para 13(c)(iv) or table.\n"
        "Look for: 'issued by', 'Government of'. Return verbatim; if not found → null.\n"
        "Return ONLY: {\"eligibility.issuer_constraints\": value}"
    ),
    "csa.regime.default": (
        "From the haircut table, determine the default rating regime. If only one regime → return it. If multiple → look for 'default', 'applicable', or use first non-null regime.\n"
        "Map: S&P→'sp', Moody’s First→'m1', Moody’s Second→'m2', Fitch→'fitch'.\n"
        "Return ONLY: {\"csa.regime.default\": 'sp'|'m1'|'m2'|'fitch'|null}"
    ),
    "haircuts.matrix": (
        "Extract ONLY from Para 13(c)(ii) table. For each row: asset_type=left column, maturity_bucket=row header, regime=column header (normalized: S&P→'sp', M1→'m1', M2→'m2', Fitch→'fitch'), valuation_percentage=float. Only include if all 4 present. Return array.\n"
        "Return ONLY: {\"haircuts.matrix\": value}"
    ),
    "caps_windows.cash_cap_pct_of_U": (
        "Extract ONLY from Para 13(c)(ii). Look for: 'Cash capped at X% of U'. Return number or null.\n"
        "Return ONLY: {\"caps_windows.cash_cap_pct_of_U\": value}"
    ),
    "caps_windows.issuer_cap": (
        "Extract ONLY from concentration limits. Look for: 'no more than X% from one issuer'. Return string or number; if not found → null.\n"
        "Return ONLY: {\"caps_windows.issuer_cap\": value}"
    ),
    "caps_windows.class_cap": (
        "Extract ONLY from concentration limits. Look for: 'asset class cap'. Return string or number; if not found → null.\n"
        "Return ONLY: {\"caps_windows.class_cap\": value}"
    ),
    "caps_windows.currency_cap": (
        "Extract ONLY from concentration limits. Look for: 'currency cap'. Return string or number; if not found → null.\n"
        "Return ONLY: {\"caps_windows.currency_cap\": value}"
    ),
    "caps_windows.global_cap": (
        "Extract ONLY from concentration limits. Look for: 'global cap'. Return string or number; if not found → null.\n"
        "Return ONLY: {\"caps_windows.global_cap\": value}"
    ),
}

GENERIC_PROMPT = """
Extract the requested field from the CSA text. Use only explicit statements; if not present, return null (or []).
Return ONLY strict JSON with the single key provided.
"""

# ========================================
# 5. BATCH EXTRACTOR + VALIDATION
# ========================================
async def extract_field(field: str, document: str, client: AsyncOpenAI) -> Dict[str, Any]:
    tmpl = PROMPT_TEMPLATES.get(field, GENERIC_PROMPT)
    # Build strict system prompt per spec
    system_prompt = (
        "You are a CSA extraction engine. Extract ONLY the requested field. "
        "Return ONLY JSON in format: {\"" + field + "\": value}. "
        "Do NOT explain. Do NOT add examples. Do NOT wrap in markdown. "
        "If not found → null. If array and empty → []."
    )
    prompt = tmpl
    max_tokens = MAX_TOKENS.get(field, 800)

    try:
        # For haircuts.matrix, scope to Paragraph 13(c)(ii) or 'Eligible Collateral (VM)' block if present
        doc_to_use = document
        regimes_hint = None
        if field == "haircuts.matrix":
            m_start = re.search(r"13\s*\(c\)\s*\(ii\)|Paragraph\s*13\s*\(c\)\s*\(ii\)|Eligible\s+Collateral\s*\(VM\)", document, re.IGNORECASE)
            if m_start:
                start = m_start.start()
                m_end = re.search(r"13\s*\(c\)\s*\(iii\)|Paragraph\s*13\s*\(c\)\s*\(iii\)|Concentration\s+Limits|caps?\s*windows?", document[m_start.end():], re.IGNORECASE)
                end = m_start.end() + (m_end.start() if m_end else min(len(document) - m_start.end(), 8000))
                doc_to_use = document[start:end]
            # Detect a 'Regimes:' line if present to force positional mapping
            m_reg = re.search(r"Regimes:\s*([a-z|]+)", doc_to_use, re.IGNORECASE)
            if m_reg:
                regimes_hint = m_reg.group(1).lower()
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{prompt}\n\n" + (f"Regimes: {regimes_hint}\nAlign each Values list positionally to these regimes.\n\n" if regimes_hint else "") + f"Document:\n{doc_to_use[:30000]}"}
            ],
            temperature=0.0,
            max_tokens=max_tokens
        )
        raw = response.choices[0].message.content.strip()
        # Try strict JSON first
        try:
            return json.loads(raw)
        except Exception:
            # Strip code fences / language hints
            txt = raw.strip()
            if txt.startswith("```"):
                txt = txt.strip("`\n ")
                nl = txt.find("\n")
                if nl != -1:
                    txt = txt[nl+1:]
            # Gracefully handle 'null' or 'None'
            if txt.lower() in ("null", "none"):
                return {field: None}
            # Extract first JSON-looking object
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", txt)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return {field: None}
            result = {field: None}
        # If haircuts.matrix came back empty, attempt a stronger retry with explicit schema hints
        if field == "haircuts.matrix":
            arr = None
            try:
                parsed = json.loads(raw)
                arr = parsed.get(field)
            except Exception:
                try:
                    arr = result.get(field)
                except Exception:
                    arr = None
            if not arr:
                hint = (
                    "You must output a JSON array of objects with keys: asset_type, maturity_bucket, regime, valuation_percentage. "
                    "Regime must be one of 'sp','m1','m2','fitch' or null. Use the provided table lines strictly; map Values positionally to Regimes order when present."
                )
                retry_prompt = f"{prompt}\n\n{hint}\n\nDocument:\n{doc_to_use[:30000]}"
                response2 = await client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": retry_prompt}
                    ],
                    temperature=0.0,
                    max_tokens=max_tokens
                )
                raw2 = response2.choices[0].message.content.strip()
                try:
                    return json.loads(raw2)
                except Exception:
                    pass
        return result
    except Exception:
        return {field: None}

async def extract_csa(html_content: str) -> CSAExtraction:
    # Lazy-initialize client using env API key; if missing, return an empty-but-valid structure
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return CSAExtraction(
            csa={"meta": {"governing_law": None, "agreement_date": None, "one_way": None}},
            parties={"party_A": {"name": None, "role": None}, "party_B": {"name": None, "role": None}},
            terms={}, eligibility={}, haircuts={}, caps_windows=CapsWindows()
        )
    # Use async context manager to ensure clean shutdown within event loop
    async with AsyncOpenAI(api_key=api_key) as client:
        text = html_to_text(html_content)
        haircuts_block = extract_13c2_block_text(html_content)
        tasks = []
        for field in FIELDS:
            if field == "haircuts.matrix" and haircuts_block:
                tabular = extract_13c2_table_text(html_content) or ""
                rows_text = extract_13c2_rows_text(html_content) or ""
                combined = (tabular + "\n\n" + rows_text).strip() or haircuts_block
                tasks.append(extract_field(field, combined, client))
            else:
                tasks.append(extract_field(field, text, client))
        results = await asyncio.gather(*tasks)
    raw = {k: v for d in results for k, v in d.items()}

    # Build nested dict
    data = {
        "csa": {"meta": {}},
        "parties": {"party_A": {}, "party_B": {}},
        "terms": {},
        "eligibility": {},
        "haircuts": {},
        "caps_windows": {}
    }
    for k, v in raw.items():
        parts = k.split(".")
        current = data
        for p in parts[:-1]:
            if p not in current:
                current[p] = {}
            current = current[p]
        current[parts[-1]] = v

    # Self-heal fallbacks for critical fields
    txt = html_to_text(html_content)
    try:
        hm = data.get("haircuts", {}).get("matrix")
        if not hm:
            fallback_rows = _fallback_parse_haircuts(html_content)
            if not fallback_rows:
                fallback_rows = _fallback_parse_haircuts_from_rows(html_content)
            if fallback_rows:
                data.setdefault("haircuts", {})["matrix"] = fallback_rows
    except Exception:
        pass
    try:
        rnd = data.setdefault("terms", {}).get("rounding") or {}
        if rnd.get("delivery") is None:
            d, r = _fallback_parse_rounding(txt)
            if d:
                rnd["delivery"] = d
            if r and rnd.get("return") is None:
                rnd["return"] = r
        data["terms"]["rounding"] = rnd
    except Exception:
        pass
    try:
        mta = data.setdefault("terms", {}).get("mta") or {}
        if mta.get("currency") is None:
            cur = _fallback_mta_currency(txt)
            if cur:
                mta["currency"] = cur
        data["terms"]["mta"] = mta
    except Exception:
        pass

    # Validate with normalization fallback
    try:
        return CSAExtraction(**data)
    except Exception:
        terms = data.get("terms", {})
        rnd = terms.get("rounding")
        if isinstance(rnd, dict):
            for key in ["delivery", "return"]:
                entry = rnd.get(key)
                if isinstance(entry, dict) and "direction" not in entry and "mode" in entry:
                    entry["direction"] = entry.pop("mode").upper()
        if terms.get("eligible_currencies") is None:
            terms["eligible_currencies"] = []
        mta = terms.get("mta")
        if isinstance(mta, dict):
            cur = mta.get("currency")
            if isinstance(cur, str):
                if cur.strip() in ["$", "US$"]:
                    mta["currency"] = "USD"
                elif len(cur) != 3:
                    mta["currency"] = None
        data["terms"] = terms
        return CSAExtraction(**data)


async def extract_csa_fields(html_content: str, fields: List[str]) -> CSAExtraction:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return CSAExtraction(
            csa={"meta": {"governing_law": None, "agreement_date": None, "one_way": None}},
            parties={"party_A": {"name": None, "role": None}, "party_B": {"name": None, "role": None}},
            terms={}, eligibility={}, haircuts={}, caps_windows=CapsWindows()
        )
    async with AsyncOpenAI(api_key=api_key) as client:
        text = html_to_text(html_content)
        tasks = [extract_field(field, text, client) for field in fields]
        results = await asyncio.gather(*tasks)
    raw = {k: v for d in results for k, v in d.items()}

    data = {
        "csa": {"meta": {}},
        "parties": {"party_A": {}, "party_B": {}},
        "terms": {},
        "eligibility": {},
        "haircuts": {},
        "caps_windows": {}
    }
    for k, v in raw.items():
        parts = k.split(".")
        current = data
        for p in parts[:-1]:
            if p not in current:
                current[p] = {}
            current = current[p]
        current[parts[-1]] = v
    return CSAExtraction(**data)

# ========================================
# 6. USAGE
# ========================================
if __name__ == "__main__":
    with open("csa.html", "r", encoding="utf-8") as f:
        html = f.read()

    result = asyncio.run(extract_csa(html))
    print(json.dumps(result.dict(), indent=2, ensure_ascii=False))