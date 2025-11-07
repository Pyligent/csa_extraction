import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
import io
try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None

# Reuse LLM field extractor for fallback (independent from html.py/core)
from .parsers.csa_llm_extraction import extract_csa_fields as llm_extract_fields


# =====================
# SPEC FIELDS (A/B/C/D)
# =====================
FIELDS: List[str] = [
    # A
    "csa.meta.governing_law", "csa.meta.agreement_date", "csa.meta.one_way",
    "parties.party_A.name", "parties.party_A.normalized_name", "parties.party_A.role",
    "parties.party_B.name", "parties.party_B.normalized_name", "parties.party_B.role",
    # B
    "terms.valuation_agent", "terms.notification_time", "terms.valuation_date", "terms.valuation_time",
    "terms.regular_settlement_day", "terms.delivery_amount", "terms.return_amount",
    "terms.mta.amount", "terms.mta.currency", "terms.rounding.delivery", "terms.rounding.return",
    "terms.dispute.notice_cutoff", "terms.dispute.resolution_timing", "terms.return_timing.days",
    # C
    "terms.base_currency", "terms.eligible_currencies", "terms.eligible_currency_includes_base", "terms.fx_haircut_pct",
    # D
    "eligibility.covered_transactions", "eligibility.spot_fx_carveout", "eligibility.ratings_condition", "eligibility.issuer_constraints",
    "csa.regime.default", "haircuts.matrix",
    "caps_windows.cash_cap_pct_of_U", "caps_windows.issuer_cap", "caps_windows.class_cap", "caps_windows.currency_cap", "caps_windows.global_cap",
]


# =====================
# HELPERS
# =====================
def _clean_party_name(raw: str) -> str:
    name = raw.strip()
    name = name.strip('"\u201C\u201D')
    name = name.strip("'")
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" ,;:.")


def _pct_to_float(x: Any) -> Optional[float]:
    s = str(x)
    m = re.search(r"([\d]+(?:\.[\d]+)?)\s*%?", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _normalize_regime(h: str) -> Optional[str]:
    if re.search(r"S&P|Standard\s*&\s*Poor", h, re.IGNORECASE):
        return "sp"
    if re.search(r"Moody.?s\s*First|\(1st\)", h, re.IGNORECASE):
        return "m1"
    if re.search(r"Moody.?s\s*Second|\(2nd\)", h, re.IGNORECASE):
        return "m2"
    if re.search(r"Fitch", h, re.IGNORECASE):
        return "fitch"
    return None


def _init_result() -> Dict[str, Any]:
    res: Dict[str, Any] = {k: (None if k != "haircuts.matrix" and k != "terms.eligible_currencies" and not k.endswith("covered_transactions") else ([] if k in ("terms.eligible_currencies", "eligibility.covered_transactions") else None)) for k in FIELDS}
    # override arrays
    res["terms.eligible_currencies"] = []
    res["eligibility.covered_transactions"] = []
    res["haircuts.matrix"] = []
    # provenance
    res["_source"] = {}
    res["_confidence"] = {}
    return res


def _get_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text


def _extract_haircuts(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if pd is None:
        return rows
    for table in soup.find_all("table"):
        try:
            s = io.StringIO(str(table))
            dfs = pd.read_html(s, header=0)
        except Exception:
            continue
        for df in dfs:
            if df is None or df.empty:
                continue
            try:
                regimes = [_normalize_regime(str(c)) for c in df.columns]
            except Exception:
                regimes = [None] * len(df.columns)
            for _, r in df.iterrows():
                try:
                    asset = str(r.iloc[0]).strip()
                except Exception:
                    continue
                # maturity guess: second col text if textual
                maturity = None
                if len(r) > 1 and not isinstance(r.iloc[1], (int, float)):
                    maturity = str(r.iloc[1]).strip()
                start_col = 2 if maturity else 1
                for i in range(start_col, len(r)):
                    pct = _pct_to_float(r.iloc[i])
                    if pct is None:
                        continue
                    rows.append({
                        "asset_type": asset,
                        "maturity_bucket": maturity or "N/A",
                        "regime": regimes[i] if i < len(regimes) else None,
                        "valuation_percentage": pct,
                    })
    return rows


def extract_regex_field(field: str, text: str, soup: BeautifulSoup, result: Dict[str, Any]) -> Any:
    # A. meta & parties
    if field == "csa.meta.governing_law":
        if re.search(r"new\s+york\s+law", text, re.IGNORECASE):
            return "NY"
        if re.search(r"english\s+law", text, re.IGNORECASE):
            return "English"
        return None
    if field == "csa.meta.agreement_date":
        m = re.search(r"dated\s+as\s+of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.IGNORECASE)
        return m.group(1) if m else None
    if field == "csa.meta.one_way":
        return True if re.search(r"Unilateral\s+Form|Single\s+Secured\s+Party|One\s+Way", text, re.IGNORECASE) else None

    if field == "parties.party_A.name":
        m = re.search(r"([A-Z][A-Za-z0-9&.,'\- ]{5,})\s*\(\s*Party\s*A\s*\)", text, re.IGNORECASE)
        return _clean_party_name(m.group(1)) if m else None
    if field == "parties.party_B.name":
        m = re.search(r"([A-Z][A-Za-z0-9&.,'\- ]{5,})\s*\(\s*Party\s*B\s*\)", text, re.IGNORECASE)
        if m:
            return _clean_party_name(m.group(1))
        m2 = re.search(r"\b([A-Z][A-Z0-9&.,'\-() ]*?TRUST\s+SERIES\s+[A-Z0-9\-]+)\b", text)
        return _clean_party_name(m2.group(1)) if m2 else None
    if field == "parties.party_A.normalized_name":
        return result.get("parties.party_A.name")
    if field == "parties.party_B.normalized_name":
        return result.get("parties.party_B.name")
    if field == "parties.party_A.role":
        return "Secured" if result.get("csa.meta.one_way") else None
    if field == "parties.party_B.role":
        return "Pledgor" if result.get("csa.meta.one_way") else None

    # B. core VM
    if field == "terms.valuation_agent":
        m = re.search(r"Valuation\s+Agent\s+means\s+(.+?)(?=\.|;)", text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "terms.notification_time":
        m = re.search(r"Notification\s+Time\s+means\s+(.+?)(?=\.|;)", text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "terms.valuation_date":
        m = re.search(r"Valuation\s+Date\s+means\s+(.+?)(?=\.|;)", text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "terms.valuation_time":
        m = re.search(r"Valuation\s+Time\s+means\s+(.+?)(?=\.|;)", text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "terms.regular_settlement_day":
        m = re.search(r"Regular\s+Settlement\s+Day\s+means\s+(.+?)(?=\.|;)", text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "terms.delivery_amount":
        m = re.search(r"Delivery\s+Amount.*?except\s+that\s+(.+?)(?=Return\s+Amount|\.)", text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else None
    if field == "terms.return_amount":
        m = re.search(r"Return\s+Amount.*?except\s+that\s+(.+?)(?=Credit\s+Support\s+Amount|\.)", text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else None
    if field == "terms.mta.amount":
        m = re.search(r"Minimum\s+Transfer\s+Amount[^\d]*([\$US\s]*)\s*([\d,]+)", text, re.IGNORECASE)
        return int(m.group(2).replace(",", "")) if m else None
    if field == "terms.mta.currency":
        # Pattern 1: explicit code before amount
        m1 = re.search(r"Minimum\s+Transfer\s+Amount[^\w]*([A-Z]{3})\s*[\$\s]*[\d,]+", text, re.IGNORECASE)
        if m1:
            return m1.group(1).upper()
        # Pattern 2: MTA with $ amount implies USD
        if re.search(r"\bMTA[^\w]*\$\s*[\d,]+", text, re.IGNORECASE):
            return "USD"
        # Pattern 3: textual USD mention
        if re.search(r"United\s+States\s+Dollars|US\s*Dollars|U\.?S\.?\s*Dollars", text, re.IGNORECASE):
            return "USD"
        return None
    if field in ("terms.rounding.delivery", "terms.rounding.return"):
        # Parse robust rounding patterns
        def parse_rounding(block: str) -> Optional[Dict[str, Any]]:
            m = re.search(r"rounded\s+(up|down)\s+to\s+the\s+nearest[^\w]+([A-Z]{3})\s*([\d,]+)", block, re.IGNORECASE)
            if m:
                return {
                    "direction": m.group(1).upper(),
                    "amount": float(m.group(3).replace(",", "")),
                    "currency": m.group(2).upper(),
                }
            m2 = re.search(r"rounded\s+(up|down)[^\w]+([\d,]+)\s+([A-Z]{3})", block, re.IGNORECASE)
            if m2:
                return {
                    "direction": m2.group(1).upper(),
                    "amount": float(m2.group(2).replace(",", "")),
                    "currency": m2.group(3).upper(),
                }
            return None

        deliv_block = re.search(r"Delivery\s+Amount[\s\S]{0,240}?rounded[\s\S]{0,240}?nearest[\s\S]{0,120}?\d", text, re.IGNORECASE)
        ret_block = re.search(r"Return\s+Amount[\s\S]{0,240}?rounded[\s\S]{0,240}?nearest[\s\S]{0,120}?\d", text, re.IGNORECASE)
        if field == "terms.rounding.delivery":
            return parse_rounding(deliv_block.group(0)) if deliv_block else None
        else:
            return parse_rounding(ret_block.group(0)) if ret_block else None
    if field == "terms.dispute.notice_cutoff":
        m = re.search(r"not\s+later\s+than\s+the\s+close\s+of\s+business\s+on\s+the\s+Local\s+Business\s+Day\s+following\s+the\s+date\s+on\s+which\s+the\s+notice\s+is\s+effective", text, re.IGNORECASE)
        return m.group(0).strip() if m else None
    if field == "terms.dispute.resolution_timing":
        m = re.search(r"Resolution\s+Time\s+means\s+(.+?)(?=\.|;)", text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "terms.return_timing.days":
        return 1 if re.search(r"Local\s+Business\s+Day\s+following\s+the\s+day\s+on\s+which\s+a\s+demand", text, re.IGNORECASE) else None

    # C. currencies & FX
    if field == "terms.base_currency":
        m = re.search(r"Base\s+Currency[^A-Za-z]*([A-Z]{3})", text, re.IGNORECASE)
        return (m.group(1).upper() if m else "USD") if re.search(r"United\s+States\s+Dollars|U\.S\.\s*Dollars", text, re.IGNORECASE) else (m.group(1).upper() if m else None)
    if field == "terms.eligible_currencies":
        m = re.search(r"Eligible\s+Currency[^A-Za-z]*([A-Z]{3}(?:\s*,\s*[A-Z]{3})*)", text, re.IGNORECASE)
        return [c.strip() for c in m.group(1).split(",")] if m else []
    if field == "terms.eligible_currency_includes_base":
        base = result.get("terms.base_currency")
        elig = result.get("terms.eligible_currencies", [])
        return (base in elig) if base and isinstance(elig, list) else None
    if field == "terms.fx_haircut_pct":
        m = re.search(r"FX\s+Haircut\s+Percentage\s*:\s*([\d.]+)%?", text, re.IGNORECASE)
        return float(m.group(1)) if m else None

    # D. eligibility & haircuts
    if field == "eligibility.covered_transactions":
        m = re.search(r"Covered\s+Transactions\s*:\s*([^.]+)", text, re.IGNORECASE)
        return [m.group(1).strip()] if m else []
    if field == "eligibility.spot_fx_carveout":
        return True if re.search(r"except\s+Spot\s+FX", text, re.IGNORECASE) else None
    if field == "eligibility.ratings_condition":
        m = re.search(r"rated\s+at\s+least\s+([^\(]+?by\s+Moody['’]?s[^,]+?S&P)", text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    if field == "eligibility.issuer_constraints":
        issuers = re.findall(r"(U\.?S\.?\s+Sovereign\s+Debt|Obligations\s+of\s+U\.?S\.?\s+GSEs|U\.?S\.?\s+Municipal\s+Obligations)", text, re.IGNORECASE)
        return ", ".join([re.sub(r"\s+", " ", i) for i in issuers]) if issuers else None
    if field == "csa.regime.default":
        matrix = result.get("haircuts.matrix", []) or []
        regimes = {r.get("regime") for r in matrix if r.get("regime")}
        if len(regimes) == 1:
            return next(iter(regimes))
        if len(regimes) == 0:
            return None
        # Multi-regime: heuristics
        cat_text = " ".join((r.get("asset_type") or "") for r in matrix)
        if re.search(r"default\s+regime|S&P.*default", cat_text, re.IGNORECASE):
            return "sp"
        if re.search(r"Moody['’]?s.*default", cat_text, re.IGNORECASE):
            return "m1"
        # Fallback: first non-null regime in first row
        for r in matrix:
            if r.get("regime"):
                return r.get("regime")
        return None
    if field == "haircuts.matrix":
        return _extract_haircuts(soup)

    # caps
    if field == "caps_windows.cash_cap_pct_of_U":
        m = re.search(r"cash\s+cap.*?([\d.]+)%", text, re.IGNORECASE)
        return float(m.group(1)) if m else None
    if field == "caps_windows.issuer_cap":
        m = re.search(r"no\s+more\s+than\s+([\d.]+)%\s+from\s+one\s+issuer", text, re.IGNORECASE)
        return (m.group(0).strip() if m else None)
    if field == "caps_windows.class_cap":
        m = re.search(r"asset\s+class\s+cap[^\d]*([\d.]+)%", text, re.IGNORECASE)
        return (m.group(0).strip() if m else None)
    if field == "caps_windows.currency_cap":
        m = re.search(r"currency\s+cap[^\d]*([\d.]+)%", text, re.IGNORECASE)
        return (m.group(0).strip() if m else None)
    if field == "caps_windows.global_cap":
        m = re.search(r"global\s+cap[^\d]*([\d.]+)%", text, re.IGNORECASE)
        return (m.group(0).strip() if m else None)

    return None


async def extract_hybridv2_async(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text = _get_text(html)
    result: Dict[str, Any] = _init_result()

    # 1) Regex/bs4 first pass
    for field in FIELDS:
        try:
            val = extract_regex_field(field, text, soup, result)
        except Exception:
            val = None
        if val is not None:
            result[field] = val
            result["_source"][field] = "regex"
            result["_confidence"][field] = 1.0

    # 2) LLM fallback only for null/empty
    missing = [f for f in FIELDS if result.get(f) in (None, [], {})]
    if missing:
        try:
            llm_part = await llm_extract_fields(html, missing)
            llm_d = llm_part.dict()
        except Exception:
            llm_d = {}
        # merge
        def _get(d: Dict[str, Any], dotted: str) -> Any:
            cur = d
            for p in dotted.split('.'):
                if not isinstance(cur, dict) or p not in cur:
                    return None
                cur = cur[p]
            return cur
        for f in missing:
            v = _get(llm_d, f)
            if v not in (None, [], {}):
                result[f] = v
                result["_source"][f] = "llm"
                result["_confidence"][f] = 0.9

    return result


def extract_hybridv2(html: str) -> Dict[str, Any]:
    return asyncio.run(extract_hybridv2_async(html))


def extract_hybridv2_from_path(path: str) -> Dict[str, Any]:
    data = Path(path).read_bytes().decode("utf-8", errors="ignore")
    return extract_hybridv2(data)


