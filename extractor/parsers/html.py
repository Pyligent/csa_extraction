from bs4 import BeautifulSoup
from typing import Dict, Any, Optional
import re
import io

from ..clean import clean_text_for_extraction

# Optional pandas for robust table parsing
try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # fallback to bs4-only parsing


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
        # Fallback to 'between X and Y' (multiline tolerant)
        m_bw = re.search(r"between\s+(.{3,200}?)\s+and\s+(.{3,200}?)(?:[\.;\n\r]|$)", text, re.IGNORECASE | re.DOTALL)
        if m_bw:
            cand_a = _clean_party_name(m_bw.group(1))
            cand_b = _clean_party_name(m_bw.group(2))
            if len(cand_a) >= 3 and len(cand_b) >= 3:
                name_a, name_b = cand_a, cand_b
        else:
            parties = _find_parties_between(text)
            if parties:
                name_a, name_b = parties
    # Additional fallback: if we find lines with (Party A/B), take previous non-empty line as name
    if not (name_a and name_b):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not name_a and re.search(r"\(\s*Party\s*A\s*\)", line, re.IGNORECASE):
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                if j >= 0:
                    name_a = _clean_party_name(lines[j])
            if not name_b and re.search(r"\(\s*Party\s*B\s*\)", line, re.IGNORECASE):
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                if j >= 0:
                    name_b = _clean_party_name(lines[j])
    # If still missing Party B, scan left context around (Party B) within same paragraph
    if not name_b:
        m_ctx = re.search(r"(.{0,160})\(\s*Party\s*B\s*\)", text, re.IGNORECASE | re.DOTALL)
        if m_ctx:
            left = m_ctx.group(1)
            # strip to last break punctuation
            left = re.split(r"[\n\r;:]", left)[-1]
            left = re.sub(r"\s+", " ", left).strip()
            # keep trailing uppercase words and series codes
            m_name = re.search(r"([A-Z][A-Z0-9&.,'\-() ]{3,})$", left)
            if m_name:
                name_b = _clean_party_name(m_name.group(1))
    # Explicit trust-series capture (e.g., PPLUS TRUST SERIES GSC-2)
    if not name_b:
        m_trust_series = re.search(r"\b([A-Z][A-Z0-9&.,'\-() ]*?TRUST\s+SERIES\s+[A-Z0-9\-]+)\b", text, re.IGNORECASE)
        if m_trust_series:
            name_b = _clean_party_name(m_trust_series.group(1).upper())
    # Special-case trust series names (e.g., PPLUS TRUST SERIES GSC-2)
    if not name_b:
        m_trust = re.search(r"(PPLUS\s+TRUST[^\n,]+)", text, re.IGNORECASE)
        if m_trust:
            name_b = _clean_party_name(m_trust.group(1))
    if name_a:
        result["parties.party_A.name"] = name_a
        result["parties.party_A.normalized_name"] = name_a
    if name_b:
        result["parties.party_B.name"] = name_b
        result["parties.party_B.normalized_name"] = name_b

    # Roles for unilateral forms
    if re.search(r"\bSingle\s+Secured\s+Party\b|One\s*Way\s*CSA|Unilateral\s+Form|Secured\s+Party\s+means\s+Party\s*A", text, re.IGNORECASE):
        result["parties.party_A.role"] = "Secured"
        result["parties.party_B.role"] = "Pledgor"

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

    # Minimum Transfer Amount (from Paragraph 13(b) block if available)
    b_block = None
    m_b_start = re.search(r"Paragraph\s*13\s*\(b\)|\(b\)\s*Credit\s+Support\s+Obligations", text, re.IGNORECASE)
    if m_b_start:
        start = m_b_start.start()
        m_b_end = re.search(r"Paragraph\s*13\s*\(c\)|\(c\)\s*Eligible|Paragraph\s*13\s*\(d\)", text[m_b_start.end():], re.IGNORECASE)
        end = m_b_start.end() + (m_b_end.start() if m_b_end else min(len(text) - m_b_start.end(), 4000))
        b_block = text[start:end]

    # Minimum Transfer Amount (explicit USD amount)
    m_mta = re.search(r"Minimum\s+Transfer\s+Amount(?:\s*\([^)]*\))?[\s\S]{0,300}?(US\$|U\.?S\.?\s*\$|USD|\$)\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    if m_mta:
        try:
            amt = float(m_mta.group(2).replace(",", ""))
            result["terms.mta.amount"] = amt
            result["terms.mta.currency"] = "USD"
        except ValueError:
            pass
    else:
        # Alternative phrasing
        m_mta2 = re.search(r"\bM(?:inimum)?\s+Transfer\s+Amount\b[\s\S]{0,300}?(?:USD|US\$|U\.?S\.?\s*\$|\$)\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
        if m_mta2:
            try:
                amt = float(m_mta2.group(1).replace(",", ""))
                result["terms.mta.amount"] = amt
                result["terms.mta.currency"] = "USD"
            except ValueError:
                pass
        else:
            # Number before currency words
            m_mta3 = re.search(r"\bM(?:inimum)?\s+Transfer\s+Amount\b[\s\S]{0,300}?([\d,]+(?:\.\d{2})?)\s*(?:U\.?S\.?\s*Dollars|USD|US\$|\$)", text, re.IGNORECASE)
            if m_mta3:
                try:
                    amt = float(m_mta3.group(1).replace(",", ""))
                    result["terms.mta.amount"] = amt
                    result["terms.mta.currency"] = "USD"
                except ValueError:
                    pass
    # Try inside 13(b) block explicitly as a fallback
    if b_block and result.get("terms.mta.amount") is None:
        m_b_mta = re.search(r"Minimum\s+Transfer\s+Amount[\s\S]{0,300}?\)?\s*(?:US\$|U\.?S\.?\s*\$|USD|\$)\s*([\d,]+(?:\.\d{2})?)", b_block, re.IGNORECASE)
        if not m_b_mta:
            m_b_mta = re.search(r"Minimum\s+Transfer\s+Amount[\s\S]{0,300}?\(\s*(?:US\$|U\.?S\.?\s*\$|USD)\s*([\d,]+)\s*\)", b_block, re.IGNORECASE)
        if m_b_mta:
            try:
                result["terms.mta.amount"] = float(m_b_mta.group(1).replace(",", ""))
                result["terms.mta.currency"] = "USD"
            except Exception:
                pass

    # Regular Settlement Day (or Exchange Date fallback snippet)
    m_rsd = re.search(r"Regular\s+Settlement\s+Day\s*[:\-\u2013\u2014]?\s*(.+?)(?:\.|\n)", text, re.IGNORECASE)
    if m_rsd:
        result["terms.regular_settlement_day"] = re.sub(r"\s+", " ", m_rsd.group(1)).strip()
    else:
        m_exd = re.search(r"Exchange\s+Date\s*[:\-\u2013\u2014]?\s*(.+?)(?:\.|\n)", text, re.IGNORECASE)
        if m_exd:
            result["terms.regular_settlement_day"] = re.sub(r"\s+", " ", m_exd.group(1)).strip()

    # Delivery Amount / Return Amount: capture Paragraph 13(b) snippets
    def extract_quoted_section(label: str) -> Optional[str]:
        pat = rf"[\u201C\"']?{re.escape(label)}[\u201D\"']?\s*(?:has\s+the\s+meaning|will|means)?[\s\S]{0,10}(.+?)\.(?=\s|$)"
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
        # fallback: take the line containing label
        m2 = re.search(rf"^.*{re.escape(label)}.*$", text, re.IGNORECASE | re.MULTILINE)
        if m2:
            return re.sub(r"\s+", " ", m2.group(0)).strip()
        return None

    da = extract_quoted_section("Delivery Amount")
    if da:
        result["terms.delivery_amount"] = da
    ra = extract_quoted_section("Return Amount")
    if ra:
        result["terms.return_amount"] = ra

    # Rounding rules (Para 13(b)(iv)) – capture within 13(b) block first, else whole text
    m_round = None
    if b_block:
        m_round = re.search(r"Rounding\.?\s*[^.]*?(Delivery\s+Amount[^.]*?)(?:and\s+the\s+Return\s+Amount([^.]*?))?\.", b_block, re.IGNORECASE)
    if not m_round:
        m_round = re.search(r"Rounding\.?\s*[^.]*?(Delivery\s+Amount[^.]*?)(?:and\s+the\s+Return\s+Amount([^.]*?))?\.", text, re.IGNORECASE)
    if m_round:
        def _parse_round(txt: str):
            amt = re.search(r"(USD|US\$|U\.?S\.?\s*\$|\$)\s*([\d,]+)", txt, re.IGNORECASE)
            if re.search(r"nearest|up", txt, re.IGNORECASE):
                direction = "UP" if re.search(r"up", txt, re.IGNORECASE) else "NEAREST"
            else:
                direction = "DOWN"
            if amt:
                try:
                    return {"direction": direction, "amount": float(amt.group(2).replace(",", "")), "currency": "USD"}
                except Exception:
                    return None
            return None
        d = _parse_round(m_round.group(1))
        r = _parse_round(m_round.group(2) or "") if m_round.group(2) else d
        if d:
            result["terms.rounding.delivery"] = d
        if r:
            result["terms.rounding.return"] = r
    else:
        # Broader 13(b) rounding phrasing without 'Rounding.' lead
        if b_block:
            m_any_round = re.search(r"rounded\s+(up|down|nearest)[\s\S]{0,60}?integral\s+multiple\s+of\s+(?:US\$|U\.?S\.?\s*\$|USD|\$)\s*([\d,]+)", b_block, re.IGNORECASE)
            if m_any_round:
                direction = m_any_round.group(1).upper()
                if direction not in ("UP", "DOWN", "NEAREST"):
                    direction = "DOWN"
                try:
                    amt = float(m_any_round.group(2).replace(",", ""))
                    rounding_obj = {"direction": direction, "amount": amt, "currency": "USD"}
                    result["terms.rounding.delivery"] = rounding_obj
                    result["terms.rounding.return"] = rounding_obj
                except Exception:
                    pass

    # Haircuts matrix from HTML tables (generic extractor)
    def _parse_haircut_tables(sp: BeautifulSoup):
        rows = []
        # Try pandas first if available
        if pd is not None:
            for table in sp.find_all("table"):
                try:
                    s = io.StringIO(table.prettify())
                    dfs = pd.read_html(s, header=0)
                except Exception:
                    continue
                for df in dfs:
                    if df is None or df.empty:
                        continue
                    # Forward fill descriptor columns to handle merged cells
                    try:
                        df = df.copy()
                        df.iloc[:, 0] = df.iloc[:, 0].ffill()
                        if df.shape[1] > 1:
                            df.iloc[:, 1] = df.iloc[:, 1].ffill()
                    except Exception:
                        pass
                    # Map regimes from columns
                    col_regimes = {}
                    for idx, col in enumerate(df.columns):
                        code = None
                        try:
                            col_label = str(col)
                        except Exception:
                            col_label = ""
                        if re.search(r"S&P|Standard\s*&\s*Poor", col_label, re.IGNORECASE):
                            code = "sp"
                        elif re.search(r"Moody.?s\s*First|\(1st\)", col_label, re.IGNORECASE):
                            code = "m1"
                        elif re.search(r"Moody.?s\s*Second|\(2nd\)", col_label, re.IGNORECASE):
                            code = "m2"
                        elif re.search(r"Fitch", col_label, re.IGNORECASE):
                            code = "fitch"
                        col_regimes[idx] = code
                    # If no regimes in headers, try detect a regime row in first few rows
                    if not any(v for v in col_regimes.values() if v):
                        header_row_idx = None
                        for r_idx in range(min(6, len(df))):
                            hit = 0
                            for c_idx in range(df.shape[1]):
                                cell = str(df.iloc[r_idx, c_idx])
                                if re.search(r"S&P|Moody.?s|Fitch", cell, re.IGNORECASE):
                                    hit += 1
                            if hit >= 2:
                                header_row_idx = r_idx
                                break
                        if header_row_idx is not None:
                            for c_idx in range(df.shape[1]):
                                cell = str(df.iloc[header_row_idx, c_idx])
                                code = None
                                if re.search(r"S&P|Standard\s*&\s*Poor", cell, re.IGNORECASE):
                                    code = "sp"
                                elif re.search(r"Moody.?s\s*First|\(1st\)", cell, re.IGNORECASE):
                                    code = "m1"
                                elif re.search(r"Moody.?s\s*Second|\(2nd\)", cell, re.IGNORECASE):
                                    code = "m2"
                                elif re.search(r"Fitch", cell, re.IGNORECASE):
                                    code = "fitch"
                                col_regimes[c_idx] = code
                            df = df.drop(index=header_row_idx).reset_index(drop=True)
                    # Assume first column is asset/maturity descriptor(s)
                    for r_idx in range(len(df)):
                        try:
                            first_cell = str(df.iloc[r_idx, 0]).strip()
                        except Exception:
                            continue
                        if not first_cell or first_cell.lower() == "nan":
                            continue
                        # Attempt to split asset and maturity if present in first two cols
                        maturity = None
                        if df.shape[1] > 1:
                            mc = str(df.iloc[r_idx, 1]).strip()
                            maturity = mc if mc and mc.lower() != "nan" else None
                        asset = first_cell.rstrip(":")
                        start_col = 2 if maturity else 1
                        for c_idx in range(start_col, df.shape[1]):
                            try:
                                cell = str(df.iloc[r_idx, c_idx]).strip()
                            except Exception:
                                continue
                            mval = re.search(r"([\d]+(?:\.[\d]+)?)\s*%?", cell)
                            if not mval:
                                continue
                            try:
                                pct = float(mval.group(1))
                            except Exception:
                                continue
                            regime = col_regimes.get(c_idx)
                            rows.append({
                                "asset_type": asset,
                                "maturity_bucket": maturity or "N/A",
                                "regime": regime,
                                "valuation_percentage": pct,
                            })
            if rows:
                return rows
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

        def is_section_title(s: str) -> bool:
            if len(s) > 200:
                return False
            # Ignore known non-asset lines
            if re.search(r"Valuation\s+Date|Posting\s+Column|Factor|Schedule|Annex", s, re.IGNORECASE):
                return False
            return bool(re.search(r"Cash|Sovereign|Government|Municipal|GSE|Agency|Obligations|Treasury|Certificates|Bonds|Notes", s, re.IGNORECASE))

        def is_maturity_label(s: str) -> bool:
            return bool(re.search(r"less\s+than\s*\d+\s*year|<\s*\d+|\d+\s*(?:to|-|–)\s*\d+\s*years?|greater\s+than\s*\d+\s*years|>\s*\d+\s*years|All\s+maturit", s, re.IGNORECASE))

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
                # Detect section/asset titles (left-most cell)
                if is_section_title(cells[0]) and not is_maturity_label(cells[0]):
                    current_asset = normalize_asset_title(cells[0].rstrip(':'))
                    # reset header regimes only if a new header row appears later
                # Data row handling
                if len(cells) >= 2 and current_asset:
                    maturity_bucket = re.sub(r"\s+", " ", cells[0]).strip()
                    if not is_maturity_label(maturity_bucket):
                        # If first cell repeats asset title, skip as data row
                        continue
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

        # Fallback: div-based parsing within Paragraph 13(c)(ii) block when tables fail
        
    def _parse_div_based_13c2(sp: BeautifulSoup) -> list[dict]:
        def _norm_code(label: str) -> Optional[str]:
            if re.search(r"S&P|Standard\s*&\s*Poor", label, re.IGNORECASE):
                return "sp"
            if re.search(r"Moody.?s\s*First|\(1st\)", label, re.IGNORECASE):
                return "m1"
            if re.search(r"Moody.?s\s*Second|\(2nd\)", label, re.IGNORECASE):
                return "m2"
            if re.search(r"Fitch", label, re.IGNORECASE):
                return "fitch"
            return None
        block_start = None
        for node in sp.find_all(text=True):
            t = (node.strip() or "").lower()
            if not t:
                continue
            if "13(c)(ii)" in t or "eligible collateral (vm)" in t:
                block_start = node.parent if hasattr(node, 'parent') else None
                break
        if not block_start:
            return []
        # Collect lines
        lines = []
        stop_patterns = re.compile(r"13\(c\)\(iii\)|concentration\s+limits|caps?\s*windows?", re.IGNORECASE)
        current = block_start
        for _ in range(0, 400):
            if current is None:
                break
            txt = current.get_text(" ", strip=True)
            if txt and stop_patterns.search(txt):
                break
            if txt:
                lines.append(txt)
            current = current.find_next_sibling()

        # Detect regime header line
        regimes: list[str] = []
        for ln in lines[:10]:
            parts = re.split(r"\s{2,}|\|", ln)
            if sum(1 for p in parts if re.search(r"S&P|Moody.?s|Fitch", p, re.IGNORECASE)) >= 2:
                for p in parts:
                    code = _norm_code(p)
                    if code:
                        regimes.append(code)
                break
        rows_out: list[dict] = []
        current_asset = None
        def is_asset(s: str) -> bool:
            return bool(re.search(r"Cash|Sovereign|Government|Municipal|GSE|Obligations|Treasury|Bonds|Notes", s, re.IGNORECASE))
        for ln in lines:
            if is_asset(ln):
                current_asset = ln.rstrip(":")
                continue
            if not current_asset:
                continue
            # maturity line with percentages
            if re.search(r"\d", ln):
                maturity = ln
                # percentages on the line
                nums = [m.group(1) for m in re.finditer(r"([\d]+(?:\.[\d]+)?)\s*%?", ln)]
                for idx, num in enumerate(nums):
                    try:
                        pct = float(num)
                    except Exception:
                        continue
                    regime = regimes[idx] if idx < len(regimes) else None
                    rows_out.append({
                        "asset_type": current_asset,
                        "maturity_bucket": maturity,
                        "regime": regime,
                        "valuation_percentage": pct,
                    })
        return rows_out

    try:
        matrix_rows = _parse_haircut_tables(soup)
        if not matrix_rows:
            matrix_rows = _parse_div_based_13c2(soup)
        result["haircuts.matrix"] = matrix_rows
    except Exception:
        pass

    # Set regime.default when exactly one regime code is present across matrix
    try:
        codes = {r.get("regime") for r in result.get("haircuts.matrix", []) if r.get("regime")}
        if len(codes) == 1:
            result["csa.regime.default"] = next(iter(codes))
    except Exception:
        pass

    # Base & Eligible Currencies
    m_base = re.search(r"Base\s+Currency\s*[:\-\u2013\u2014]?\s*(USD|US\s*Dollars|U\.S\.\s*Dollars|United\s+States\s+Dollars)", text, re.IGNORECASE)
    if m_base:
        result["terms.base_currency"] = "USD"
    m_elig = re.search(r"Eligible\s+Currenc(?:y|ies)\s*[:\-\u2013\u2014]?\s*([A-Z]{3}|USD|US\s*Dollars|U\.S\.\s*Dollars|United\s+States\s+Dollars)", text, re.IGNORECASE)
    if m_elig:
        result["terms.eligible_currencies"] = ["USD"]
        if result.get("terms.base_currency") == "USD":
            result["terms.eligible_currency_includes_base"] = True

    # Covered Transactions
    m_cov = re.search(r"Covered\s+Transactions?\s*[:\-\u2013\u2014]?[\s\S]{0,120}?([A-Za-z ]*Transactions?)", text, re.IGNORECASE)
    if m_cov:
        cov = m_cov.group(1).strip()
        if re.fullmatch(r"Transactions?", cov, re.IGNORECASE):
            result["eligibility.covered_transactions"] = ["Transaction" if cov.lower().startswith("transaction") else cov]
        elif cov:
            result["eligibility.covered_transactions"] = [cov]

    # Dispute Notice Cutoff specific wording
    m_nc = re.search(r"not\s+later\s+than\s+the\s+close\s+of\s+business\s+on\s+the\s+Local\s+Business\s+Day\s+following\s+the\s+date\s+on\s+which\s+the\s+notice\s+is\s+effective", text, re.IGNORECASE)
    if m_nc:
        result["terms.dispute.notice_cutoff"] = re.sub(r"\s+", " ", m_nc.group(0))

    # Ratings / issuer constraints (coarse extraction)
    issuer_items = []
    if re.search(r"U\.?S\.?\s+Sovereign\s+Debt", text, re.IGNORECASE):
        issuer_items.append("U.S. Sovereign Debt")
    if re.search(r"Obligations\s+of\s+U\.?S\.?\s+GSEs", text, re.IGNORECASE):
        issuer_items.append("Obligations of U.S. GSEs")
    if re.search(r"U\.?S\.?\s+Municipal\s+Obligations", text, re.IGNORECASE):
        issuer_items.append("U.S. Municipal Obligations")
    if issuer_items:
        result["eligibility.issuer_constraints"] = ", ".join(issuer_items)
    m_rate = re.search(r"rated\s+at\s+least\s+([^\n\.;]+?)(?:\.|;|\n)", text, re.IGNORECASE)
    if m_rate:
        result["eligibility.ratings_condition"] = re.sub(r"\s+", " ", m_rate.group(1)).strip()

    # Regular Settlement Day from Para 12 or 13(e)
    if not result.get("terms.regular_settlement_day"):
        if re.search(r"Regular\s+Settlement\s+Day\s+means\s+Local\s+Business\s+Day", text, re.IGNORECASE):
            result["terms.regular_settlement_day"] = "Local Business Day"
        elif re.search(r"on\s+the\s+Local\s+Business\s+Day\s+following\s+the\s+day\s+on\s+which\s+a\s+demand\s+is\s+made", text, re.IGNORECASE):
            result["terms.regular_settlement_day"] = "Local Business Day"

    # Return timing days from Para 13(e)
    if result.get("terms.return_timing.days") is None:
        if re.search(r"Local\s+Business\s+Day\s+following", text, re.IGNORECASE):
            result["terms.return_timing.days"] = 1

    return result
