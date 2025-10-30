from bs4 import BeautifulSoup
from typing import Dict, Any, Optional
import re

from ..clean import clean_text_for_extraction


def init_result() -> Dict[str, Any]:
    return {
        "csa.meta.governing_law": None,
        "csa.meta.agreement_date": None,
        "csa.meta.one_way": None,
        "parties.party_A.name": None,
        "parties.party_A.normalized_name": None,
        "parties.party_B.name": None,
        "parties.party_B.normalized_name": None,
        "parties.party_A.role": None,
        "parties.party_B.role": None,
        "terms.valuation_agent": None,
        "terms.notification_time": None,
        "terms.valuation_date": None,
        "terms.valuation_time": None,
        "terms.regular_settlement_day": None,
        "terms.delivery_amount": None,
        "terms.return_amount": None,
        "terms.mta.amount": None,
        "terms.mta.currency": None,
        "terms.rounding.delivery": None,
        "terms.rounding.return": None,
        "terms.dispute.notice_cutoff": None,
        "terms.dispute.resolution_timing": None,
        "terms.return_timing.days": None,
        "terms.base_currency": None,
        "terms.eligible_currencies": [],
        "terms.eligible_currency_includes_base": None,
        "terms.fx_haircut_pct": None,
        "eligibility.covered_transactions": [],
        "eligibility.spot_fx_carveout": None,
        "eligibility.ratings_condition": None,
        "eligibility.issuer_constraints": None,
        "csa.regime.default": None,
        "haircuts.matrix": [],
        "caps_windows.cash_cap_pct_of_U": None,
        "caps_windows.issuer_cap": None,
        "caps_windows.class_cap": None,
        "caps_windows.currency_cap": None,
        "caps_windows.global_cap": None,
        "paragraph_13": {
            "title": None,
            "a_obligations": None,
            "b_i_credit_support_obligations": None,
            "b_ii_eligible_collateral_text": None,
        },
    }


def _clean_party_name(raw: str) -> str:
    name = raw.strip().strip('"\'\u201C\u201D').strip()
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(' ,;:.')


def _find_parties_between(text: str) -> Optional[tuple]:
    m = re.search(r"between\s+(.+?)\s+and\s+(.+?)(?:\.|\n|\r|;|,|$)", text, re.IGNORECASE)
    if not m:
        return None
    left, right = _clean_party_name(m.group(1)), _clean_party_name(m.group(2))
    if len(left) >= 3 and len(right) >= 3 and re.search(r"[A-Za-z]", left) and re.search(r"[A-Za-z]", right):
        return left, right
    return None


def parse_html(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    text = clean_text_for_extraction(text)

    result = init_result()

    # Governing law (minimal heuristics)
    if re.search(r"governed\s+by\s+the\s+laws\s+of\s+the\s+state\s+of\s+new\s+york|new\s+york\s+law|laws\s+of\s+the\s+state\s+of\s+new\s+york", text, re.IGNORECASE):
        result["csa.meta.governing_law"] = "NY"
    elif re.search(r"english\s+law|laws\s+of\s+england\s+and\s+wales", text, re.IGNORECASE):
        result["csa.meta.governing_law"] = "English"

    # Agreement date
    m_date = (
        re.search(r"dated\s+as\s+of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.IGNORECASE)
        or re.search(r"as\s+of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.IGNORECASE)
        or re.search(r"dated\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.IGNORECASE)
    )
    if m_date:
        result["csa.meta.agreement_date"] = m_date.group(1)

    # Parties — prefer explicit parenthetical labels like NAME ("Party A/B")
    q = '"\u201C\u201D\x93\x94'
    pat_a = re.compile(rf"([A-Z][A-Za-z0-9&.,'\-() \n]{{3,}}?)\s*\(\s*[{q}]?Party\s*A[{q}]?\s*\)", re.DOTALL)
    pat_b = re.compile(rf"([A-Z][A-Za-z0-9&.,'\-() \n]{{3,}}?)\s*\(\s*[{q}]?Party\s*B[{q}]?\s*\)", re.DOTALL)
    name_a = None
    name_b = None
    ma = pat_a.search(text)
    if ma:
        name_a = _clean_party_name(ma.group(1))
        if re.search(r"\bbetween\b", name_a, re.IGNORECASE):
            name_a = re.split(r"\bbetween\b", name_a, flags=re.IGNORECASE)[-1].strip()
    mb = pat_b.search(text)
    if mb:
        name_b = _clean_party_name(mb.group(1))
        if re.search(r"\bbetween\b", name_b, re.IGNORECASE):
            name_b = re.split(r"\bbetween\b", name_b, flags=re.IGNORECASE)[-1].strip()
    if not (name_a and name_b):
        # Fallback to 'between X and Y'
        parties = _find_parties_between(text)
        if parties:
            name_a, name_b = parties
    if name_a:
        result["parties.party_A.name"] = name_a
        result["parties.party_A.normalized_name"] = name_a
    if name_b:
        result["parties.party_B.name"] = name_b
        result["parties.party_B.normalized_name"] = name_b

    # One-way CSA explicit
    if re.search(r"\bOne\s+Way\s+CSA\b|Single\s+Secured\s+Party\b", text, re.IGNORECASE):
        result["csa.meta.one_way"] = True

    # Helper: extract definition sentences like 'Label' means ... .
    def extract_definition(label: str) -> Optional[str]:
        label_pattern = rf"[\"\u201C\u201D]?{re.escape(label)}[\"\u201C\u201D]?\s*means\s*(.+?)\."
        m = re.search(label_pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            val = re.sub(r"\s+", " ", m.group(1).strip())
            val = re.sub(r"[\u0093\u0094]", "", val)
            return val
        # Fallback: find 'label' nearby followed by 'means' and take to period
        m2 = re.search(rf"{re.escape(label)}[\s\S]{{0,120}}?means\s*([\s\S]+?)\.", text, re.IGNORECASE)
        if m2:
            val = re.sub(r"\s+", " ", m2.group(1).strip())
            val = re.sub(r"[\u0093\u0094]", "", val)
            return val
        return None

    def extract_time_phrase(label: str) -> Optional[str]:
        # Try local window after label and grab time phrase until next period
        m = re.search(rf"{re.escape(label)}", text, re.IGNORECASE)
        if m:
            window = text[m.start(): m.start() + 400]
            mtime = re.search(r"(\d{1,2}:\d{2}\s*(?:a|p)\.m\.[^\.]*)", window, re.IGNORECASE)
            if mtime:
                return re.sub(r"\s+", " ", mtime.group(1).strip())
        # Fallback to sentence-based extractor
        pat = rf"[\"\u201C\u201D]?{re.escape(label)}[\"\u201C\u201D]?\s*means\s*([\s\S]*?\d{{1,2}}:\d{{2}}\s*(?:a|p)\.m\.[^\.]*?)\."
        m2 = re.search(pat, text, re.IGNORECASE)
        if m2:
            return re.sub(r"\s+", " ", m2.group(1).strip())
        return None

    # Core VM definitions
    val_agent = extract_definition("Valuation Agent")
    notif_time = extract_time_phrase("Notification Time") or extract_definition("Notification Time")
    val_date = extract_definition("Valuation Date")
    val_time = extract_definition("Valuation Time")
    if val_agent:
        result["terms.valuation_agent"] = val_agent
    if notif_time:
        result["terms.notification_time"] = notif_time
    if val_date:
        result["terms.valuation_date"] = val_date
    if val_time:
        result["terms.valuation_time"] = val_time

    # Dispute Resolution – Resolution Time
    res_time = extract_time_phrase("Resolution Time") or extract_definition("Resolution Time")
    if res_time:
        result["terms.dispute.resolution_timing"] = res_time

    # Rounding (Delivery up / Return down)
    m_round = re.search(r"Rounding\.[\s\S]*?(Delivery\s*Amount[\s\S]*?)and\s*the\s*Return\s*Amount([\s\S]*?)\.", text, re.IGNORECASE)
    if m_round:
        d_txt = m_round.group(1)
        r_txt = m_round.group(2)
        def parse_ccy_amt(s: str):
            m = re.search(r"(US\$|USD|\$)\s*([\d,]+)", s, re.IGNORECASE)
            if not m:
                return None
            amt = float(m.group(2).replace(",", ""))
            return {"currency": "USD", "amount": amt}
        d = parse_ccy_amt(d_txt)
        r = parse_ccy_amt(r_txt)
        if d:
            mode = "UP" if re.search(r"rounded\s+up|up\s+to\s+the\s+nearest", d_txt, re.IGNORECASE) else "NEAREST"
            result["terms.rounding.delivery"] = {"mode": mode if mode != "NEAREST" else "NEAREST", "amount": d["amount"], "currency": d["currency"]}
        if r:
            mode = "DOWN" if re.search(r"rounded\s+down|down\s+to\s+the\s+nearest", r_txt, re.IGNORECASE) else "NEAREST"
            result["terms.rounding.return"] = {"mode": mode if mode != "NEAREST" else "NEAREST", "amount": r["amount"], "currency": r["currency"]}

    # Minimum Transfer Amount (explicit USD amount)
    m_mta = re.search(r"Minimum\s+Transfer\s+Amount[\s\S]{0,120}?(US\$|USD|\$)\s*([\d,]+)", text, re.IGNORECASE)
    if m_mta:
        try:
            amt = float(m_mta.group(2).replace(",", ""))
            result["terms.mta.amount"] = amt
            result["terms.mta.currency"] = "USD"
        except ValueError:
            pass

    # Haircuts matrix from HTML tables (generic extractor)
    def _parse_haircut_tables(sp: BeautifulSoup):
        rows = []
        current_asset: Optional[str] = None
        header_cells: Optional[list[str]] = None

        def pct_to_float(s: str) -> Optional[float]:
            m = re.search(r"([\d]+(?:\.[\d]+)?)\s*%", s)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    return None
            m2 = re.search(r"^([\d]+(?:\.[\d]+)?)$", s)
            if m2:
                try:
                    return float(m2.group(1))
                except ValueError:
                    return None
            return None

        def normalize_asset_title(s: str) -> str:
            s = re.sub(r"\s+", " ", s).strip()
            return s

        def is_section_title(s: str) -> bool:
            if len(s) > 200:
                return False
            if re.search(r"Moody.?s\s+.*Factor|Posting\s+Column|Valuation\s+Date", s, re.IGNORECASE):
                return False
            return bool(re.search(r"Cash|Government|Commercial\s+Paper|Bonds|Certificates|Treasury|Euro|Sterling|Agency", s, re.IGNORECASE))

        def normalize_regime_code(label: str) -> Optional[str]:
            if re.search(r"S&P|Standard\s*&\s*Poor", label, re.IGNORECASE):
                return "sp"
            if re.search(r"Moody.?s\s*First|Moody.?s\s*\(1st\)", label, re.IGNORECASE):
                return "m1"
            if re.search(r"Moody.?s\s*Second|Moody.?s\s*\(2nd\)", label, re.IGNORECASE):
                return "m2"
            if re.search(r"Fitch", label, re.IGNORECASE):
                return "fitch"
            return None

        def detect_regimes(headers: list[str]) -> dict[int, Optional[str]]:
            mapping: dict[int, Optional[str]] = {}
            for idx, h in enumerate(headers):
                code = normalize_regime_code(h.strip())
                mapping[idx] = code  # may be None for unrecognized headers
            return mapping

        for table in sp.find_all("table"):
            header_cells = None
            current_asset = None
            # Quick screen
            ttxt = table.get_text(" ", strip=True)
            if not re.search(r"\d", ttxt):
                continue
            # Iterate rows
            for tr in table.find_all("tr"):
                cells_raw = tr.find_all(["td", "th"])
                cells = [c.get_text(" ", strip=True) for c in cells_raw]
                if not cells:
                    continue
                # Consider the first non-empty header row as header
                if header_cells is None:
                    if any(cells_raw[i].name == 'th' for i in range(len(cells_raw))):
                        header_cells = cells
                        regimes_map = detect_regimes(header_cells)
                        continue
                    # Also treat first row as header if it contains regime keywords
                    if any(re.search(r"S&P|Moody.?s|Fitch", c, re.IGNORECASE) for c in cells):
                        header_cells = cells
                        regimes_map = detect_regimes(header_cells)
                        continue
                # Detect section/asset titles
                if is_section_title(cells[0]):
                    current_asset = normalize_asset_title(cells[0])
                    # reset header regimes only if a new header row appears later
                # Data row handling
                if len(cells) >= 2 and current_asset:
                    maturity_bucket = re.sub(r"\s+", " ", cells[0]).strip()
                    # Iterate data cells with possible regime association
                    for idx, cell in enumerate(cells[1:], start=1):
                        val = pct_to_float(cell)
                        if val is None:
                            continue
                        regime_code: Optional[str] = None
                        if header_cells is not None and idx < len(header_cells):
                            regime_code = detect_regimes(header_cells).get(idx)
                        # if only one column of data and no recognizable header, leave regime_code None
                        rows.append({
                            "asset_type": current_asset,
                            "maturity_bucket": maturity_bucket,
                            "regime": regime_code,
                            "valuation_percentage": val,
                        })
        return rows

    try:
        result["haircuts.matrix"] = _parse_haircut_tables(soup)
    except Exception:
        pass

    return result
