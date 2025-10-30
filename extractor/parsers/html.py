from bs4 import BeautifulSoup
from ..utils import (
    find_anchor,
    extract_paragraph,
    parse_percentage,
    normalize_regime,
)
from ..clean import clean_text_for_extraction
from typing import List, Dict, Any, Optional
import re


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
# Entity heuristics used to validate party names across helpers
ENTITY_TOKENS = [
    "INC", "LLC", "LTD", "PLC", "BANK", "ASSOCIATION", "NATIONAL", "TRUST",
    "CORPORATION", "COMPANY", "NA", "N.A.", "S.A.", "AG", "BV", "LP", "LLP",
]


def _is_entity_like_name(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for c in letters if c.isupper()) / max(1, len(letters))
    has_token = any(t in s.upper() for t in ENTITY_TOKENS)
    return upper_ratio >= 0.5 or has_token



def parse_html(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    text = clean_text_for_extraction(text)
result = init_result()

    # A. Document & Parties
    if re.search(r"\bnew\s+york\s+law\b|governed\s+by\s+the\s+laws\s+of\s+the\s+state\s+of\s+new\s+york", text, re.IGNORECASE):
result["csa.meta.governing_law"] = "NY"
    elif re.search(r"\benglish\s+law\b|governed\s+by\s+english\s+law|laws\s+of\s+england\s+and\s+wales", text, re.IGNORECASE):
result["csa.meta.governing_law"] = "English"

    date_match = re.search(r"dated\s+as\s+of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I) or \
                 re.search(r"as\s+of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I) or \
                 re.search(r"dated\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
if date_match:
result["csa.meta.agreement_date"] = date_match.group(1)

    # Party names (robust matching around preamble/signature; avoid generic label lines)
    parties_between = _find_parties_between(text)
    name_a = name_b = None
    if parties_between:
        name_a, name_b = parties_between
    else:
        paren = _find_parties_by_parenthetical_labels(text)
        if paren:
            name_a, name_b = paren
        else:
            labeled = _find_parties_by_labeled_lines(text)
            if labeled:
                name_a, name_b = labeled
            else:
                sig_names = _find_parties_from_signature(text)
                if sig_names:
                    name_a, name_b = sig_names
    if name_a:
        result["parties.party_A.name"] = name_a
        result["parties.party_A.normalized_name"] = name_a
    if name_b:
        result["parties.party_B.name"] = name_b
        result["parties.party_B.normalized_name"] = name_b

    # Only mark one_way when explicit; avoid weak heuristics
    if re.search(r"single\s+secured\s+party\b|one\s*-?\s*way\b|only\s+party\s+a\s+will\s+be\s+required|only\s+party\s+b\s+will\s+be\s+required", text, re.IGNORECASE):
result["csa.meta.one_way"] = True

    # B. Core VM
    core_anchors = [
        "Valuation Agent",
        "Notification Time",
        "Valuation Date",
        "Valuation Time",
        "Regular Settlement Day",
        "Delivery Amount",
        "Return Amount",
        "Minimum Transfer Amount",
        "Rounding",
        "Dispute Resolution",
        "Exchange Date",
    ]

    def stops_except(current: str) -> List[str]:
        return [a for a in core_anchors if a != current]

    # Extract concise field values (prefer line-scoped captures)
    result["terms.valuation_agent"] = _extract_anchor_snippet(text, "Valuation Agent") or _extract_field_line_value(text, "Valuation Agent") or _extract(text, ["Valuation Agent"], stops_except("Valuation Agent"))
    result["terms.notification_time"] = _extract_anchor_snippet(text, "Notification Time") or _extract_field_line_value(text, "Notification Time") or _extract(text, ["Notification Time"], stops_except("Notification Time"))
    # Only keep Notification Time if an explicit time expression is present; otherwise abstain
    if result["terms.notification_time"]:
        if not re.search(r"\b\d{1,2}:\d{2}\s*(?:a|p)\.m\.", result["terms.notification_time"], re.IGNORECASE):
            result["terms.notification_time"] = None
    result["terms.valuation_date"] = _extract_anchor_snippet(text, "Valuation Date") or _extract_field_line_value(text, "Valuation Date") or _extract(text, ["Valuation Date"], stops_except("Valuation Date"))
    result["terms.valuation_time"] = _extract_anchor_snippet(text, "Valuation Time") or _extract_field_line_value(text, "Valuation Time") or _extract(text, ["Valuation Time"], stops_except("Valuation Time"))
    result["terms.regular_settlement_day"] = _extract_anchor_snippet(text, "Regular Settlement Day") or _extract_field_line_value(text, "Regular Settlement Day") or _extract(text, ["Regular Settlement Day"], stops_except("Regular Settlement Day"))
    if not result["terms.valuation_time"]:
        m_vt = re.search(r"(close of business[^\.\n;]*)", text, re.IGNORECASE)
        if m_vt:
            result["terms.valuation_time"] = m_vt.group(1).strip() + "."
    if not result["terms.regular_settlement_day"]:
        alt_rs = _extract_anchor_snippet(text, "Regular Settlement") or _extract_field_line_value(text, "Regular Settlement")
        if alt_rs:
            result["terms.regular_settlement_day"] = alt_rs
    result["terms.delivery_amount"] = _extract(text, ["Delivery Amount"], stops_except("Delivery Amount")) or _extract(text, ["Delivery Amount (VM)"], stops_except("Delivery Amount (VM)"))
    result["terms.return_amount"] = _extract(text, ["Return Amount"], stops_except("Return Amount")) or _extract(text, ["Return Amount (VM)"], stops_except("Return Amount (VM)"))

    # MTA amount and currency
    def parse_currency_amount(block: str) -> Optional[tuple]:
        if not block:
            return None
        # Patterns like USD 100,000 or $100,000
        m = re.search(r"\b(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|HKD|[A-Z]{3})\s*([\d,]+(?:\.\d+)?)\b", block)
        if m:
            return float(m.group(2).replace(",", "")), m.group(1)
        m = re.search(r"([$€£])\s*([\d,]+(?:\.\d+)?)\b", block)
        if m:
            sym_to_ccy = {"$": "USD", "€": "EUR", "£": "GBP"}
            return float(m.group(2).replace(",", "")), sym_to_ccy.get(m.group(1), "USD")
        # Amount first, then currency
        m = re.search(r"\b([\d,]+(?:\.\d+)?)\s*(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|HKD|[A-Z]{3})\b", block)
        if m:
            return float(m.group(1).replace(",", "")), m.group(2)
        return None

    mta_block = _extract(text, ["Minimum Transfer Amount"], stops_except("Minimum Transfer Amount"))
    if mta_block:
        ca = parse_currency_amount(mta_block)
        if ca:
            amount, ccy = ca
            result["terms.mta.amount"] = amount
            result["terms.mta.currency"] = ccy

    # C. Currencies & FX
    # Base Currency
    base_block = _extract(text, ["Base Currency (VM)", "Base Currency"], stops_except("Base Currency"))
    def _parse_base_currency(block: Optional[str]) -> Optional[str]:
        if not block:
            return None
        m = re.search(r"\b(USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|HKD|SGD|NOK|SEK|DKK|ZAR|NZD)\b", block)
        if m:
            return m.group(1)
        name_map = {
            "U.S. dollars": "USD", "US dollars": "USD", "United States dollars": "USD", "dollars": "USD",
            "euro": "EUR", "pounds sterling": "GBP", "sterling": "GBP", "yen": "JPY",
        }
        for k, v in name_map.items():
            if re.search(rf"\b{k}\b", block, re.IGNORECASE):
                return v
        return None
    base_ccy = _parse_base_currency(base_block)
    if not base_ccy:
        # Fallback: explicit inline mention after label (no section)
        m_base = re.search(r"Base\s+Currency[^A-Za-z0-9]*([A-Z]{3}|U\.S\.\s*Dollars|US\s*Dollars|United\s*States\s*Dollars|Euro|Pounds\s*Sterling|Yen)", text, re.IGNORECASE)
        if m_base:
            token = m_base.group(1)
            name_map2 = {
                "U.S. DOLLARS": "USD", "US DOLLARS": "USD", "UNITED STATES DOLLARS": "USD",
                "EURO": "EUR", "POUNDS STERLING": "GBP", "YEN": "JPY",
            }
            base_ccy = token if re.fullmatch(r"[A-Z]{3}", token, re.IGNORECASE) else name_map2.get(token.upper())

    # Eligible currencies
    elig_block = _extract(text, ["Eligible Currency (VM)", "Eligible Currency"], stops_except("Eligible Currency"))
    def _parse_currency_list(block: Optional[str]) -> Optional[List[str]]:
        if not block:
            return None
        codes = set(re.findall(r"\b([A-Z]{3})\b", block))
        valid_set = {
            "USD","EUR","GBP","JPY","CAD","AUD","CHF","CNY","HKD","SGD","NOK","SEK","DKK","ZAR","NZD"
        }
        valid = {c for c in codes if c in valid_set}
        mapping = {
            "dollar": "USD", "dollars": "USD", "u.s. dollars": "USD", "us dollars": "USD",
            "euro": "EUR", "sterling": "GBP", "pounds": "GBP", "yen": "JPY"
        }
        for k, v in mapping.items():
            if re.search(rf"\b{k}\b", block, re.IGNORECASE):
                valid.add(v)
        out = sorted(valid)
        return out or None
    elig_list = _parse_currency_list(elig_block)
    if elig_list is None:
        # Fallback: capture inline list after label
        m_elig = re.search(r"Eligible\s+Currenc(?:y|ies)[^:]*:\s*([^\n\r\.]+)", text, re.IGNORECASE)
        if m_elig:
            segment = m_elig.group(1)
            # Parse codes and common names
            codes = set(re.findall(r"\b([A-Z]{3})\b", segment))
            name_map3 = {"dollars": "USD", "u.s. dollars": "USD", "us dollars": "USD", "euro": "EUR", "sterling": "GBP", "yen": "JPY"}
            for k, v in name_map3.items():
                if re.search(rf"\b{k}\b", segment, re.IGNORECASE):
                    codes.add(v)
            elig_list = sorted(c for c in codes if re.fullmatch(r"[A-Z]{3}", c)) or None

    # includes Base Currency
    includes_base = None
    inc_block = (base_block or "") + "\n" + (elig_block or "")
    if re.search(r"includes?\s+Base\s+Currency", inc_block, re.IGNORECASE) or re.search(r"including\s+the\s+Base\s+Currency", inc_block, re.IGNORECASE):
        includes_base = True
    # If both base and eligible list are explicit, derive boolean explicitly
    if includes_base is None and base_ccy and elig_list is not None:
        includes_base = base_ccy in elig_list

    # FX haircut percent
    fx_block = _extract(text, ["FX Haircut Percentage", "FX Haircut", "Cross-Currency Haircut", "Cross-currency haircut"], stops_except("FX Haircut")) or ""
    if not re.search(r"N/?A|as\s+agreed", fx_block, re.IGNORECASE):
        fx_match = (
            re.search(r"([\d]{1,2}(?:\.\d+)?)\s*%", fx_block, re.IGNORECASE)
            or re.search(r"([\d]{1,2}(?:\.\d+)?)\s*percent", fx_block, re.IGNORECASE)
        )
        if fx_match:
            try:
                result["terms.fx_haircut_pct"] = float(fx_match.group(1))
            except ValueError:
                pass

    if base_ccy:
        result["terms.base_currency"] = base_ccy
    if elig_list:
        result["terms.eligible_currencies"] = elig_list
    if includes_base:
        result["terms.eligible_currency_includes_base"] = True

    # Rounding (VM)
    def parse_rounding(block: Optional[str]) -> Optional[Dict[str, Any]]:
        if not block:
            return None
        out: Dict[str, Any] = {}
        def find_for(label: str) -> Optional[Dict[str, Any]]:
            # e.g., Delivery Amount rounded up to the nearest integral multiple of USD $10,000
            m = re.search(
                rf"{label}[^\n\r]*?rounded[^\n\r]*?(nearest|up|down)[^\n\r]*?(USD|EUR|GBP|[A-Z]{{3}}|[$€£])\s*(?:[$€£])?\s*([\d,]+)",
                block,
                re.IGNORECASE,
            )
            if not m:
                return None
            mode = m.group(1).upper()
            ccy_raw = m.group(2)
            amt = float(m.group(3).replace(",", ""))
            ccy = {"$": "USD", "€": "EUR", "£": "GBP"}.get(ccy_raw, ccy_raw)
            return {"mode": "NEAREST" if "NEAREST" in mode else mode, "amount": amt, "currency": ccy}
        d = find_for("Delivery") or find_for("Delivery Amount")
        r = find_for("Return") or find_for("Return Amount")
        if d or r:
            out["delivery"] = d
            out["return"] = r
            return out
        return None

    rounding_block = _extract(text, ["Rounding"], stops_except("Rounding")) or _extract(text, ["Rounding (VM)"], stops_except("Rounding (VM)"))
    pr = parse_rounding(rounding_block)
    if pr:
        result["terms.rounding.delivery"] = pr.get("delivery")
        result["terms.rounding.return"] = pr.get("return")
    else:
        # Fallback: search globally
        def parse_ccy_amount(tok: str) -> Optional[tuple]:
            m = re.search(r"(USD|EUR|GBP|[A-Z]{3}|[$€£])\s*(?:[$€£])?\s*([\d,]+)", tok, re.IGNORECASE)
            if not m:
                return None
            ccy_raw = m.group(1)
            amt = float(m.group(2).replace(",", ""))
            ccy = {"$": "USD", "€": "EUR", "£": "GBP"}.get(ccy_raw.upper(), ccy_raw.upper())
            return ccy, amt

        m_del = re.search(r"Delivery Amount[^\n\r]*?rounded\s+(nearest|up|down)[^\n\r]*?((?:USD|EUR|GBP|[A-Z]{3}|[$€£])\s*(?:[$€£])?\s*[\d,]+)", text, re.IGNORECASE)
        if m_del:
            ca = parse_ccy_amount(m_del.group(2))
            if ca:
                ccy, amt = ca
                result["terms.rounding.delivery"] = {"mode": m_del.group(1).upper(), "amount": amt, "currency": ccy}
        m_ret = re.search(r"Return Amount[\s\S]*?rounded\s+(nearest|up|down)[\s\S]*?((?:USD|EUR|GBP|[A-Z]{3}|[$€£])\s*(?:[$€£])?\s*[\d,]+)", text, re.IGNORECASE)
        if m_ret:
            ca = parse_ccy_amount(m_ret.group(2))
            if ca:
                ccy, amt = ca
                result["terms.rounding.return"] = {"mode": m_ret.group(1).upper(), "amount": amt, "currency": ccy}

    # Dispute: Resolution Time and Notice Cutoff
    res_time_snip = _extract_anchor_snippet(text, "Resolution Time")
    if res_time_snip:
        result["terms.dispute.resolution_timing"] = res_time_snip
    else:
        dispute_block = _extract(text, ["Dispute Resolution (VM)"], stops_except("Dispute Resolution (VM)")) or _extract(text, ["Dispute Resolution"], stops_except("Dispute Resolution"))
        if dispute_block:
            result["terms.dispute.resolution_timing"] = dispute_block
    notice_ctx = _extract(text, ["Dispute Resolution (VM)"], stops_except("Dispute Resolution (VM)")) or _extract(text, ["Paragraph 5"], stops_except("Paragraph 5")) or ""
    notice_line = None
    for l in notice_ctx.splitlines():
        if re.search(r"Notification Time", l, re.IGNORECASE):
            notice_line = l.strip()
            break
    def _find_time_expr(s: str) -> Optional[str]:
        if not s:
            return None
        m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:a|p)\.m\.,?\s*[A-Za-z\s]*time)\b", s, re.IGNORECASE)
        return m.group(1) if m else None
    explicit_time = _find_time_expr(notice_line)
    if explicit_time:
        result["terms.dispute.notice_cutoff"] = explicit_time
    else:
        nt_block = _extract_anchor_snippet(text, "Notification Time") or _extract(text, ["Notification Time"], stops_except("Notification Time")) or ""
        notif_value = _find_time_expr(nt_block)
        if notice_line and re.search(r"by\s+the\s+Notification\s+Time|by\s+Notification\s+Time", notice_line, re.IGNORECASE) and notif_value:
            result["terms.dispute.notice_cutoff"] = notif_value

    # Return timing days: search Return Amount or Transfer Timing context for explicit phrases
    ret_ctx = result.get("terms.return_amount") or _extract_anchor_snippet(text, "Return Amount") or ""
    trans_timing_ctx = _extract_anchor_snippet(text, "Transfer Timing") or ""
    search_ctx = ret_ctx + "\n" + trans_timing_ctx
    if re.search(r"Local\s+Business\s+Day\s+following", search_ctx, re.IGNORECASE) or re.search(r"next\s+Local\s+Business\s+Day", search_ctx, re.IGNORECASE):
        result["terms.return_timing.days"] = 1
    else:
        m_two = re.search(r"two\s+Local\s+Business\s+Days|second\s+Local\s+Business\s+Day", search_ctx, re.IGNORECASE)
        if m_two:
            result["terms.return_timing.days"] = 2
        else:
            m_num = re.search(r"(\d+)\s+Local\s+Business\s+Days", search_ctx, re.IGNORECASE)
            if m_num:
                try:
                    result["terms.return_timing.days"] = int(m_num.group(1))
                except ValueError:
                    pass
            else:
                # Fallback: search full text for explicit Transfer Timing rules
                if re.search(r"demand\s+.*?by\s+the\s+Notification\s+Time[\s\S]{0,200}next\s+Local\s+Business\s+Day", text, re.IGNORECASE):
                    result["terms.return_timing.days"] = 1
                elif re.search(r"demand\s+.*?after\s+the\s+Notification\s+Time[\s\S]{0,200}second\s+Local\s+Business\s+Day", text, re.IGNORECASE):
                    result["terms.return_timing.days"] = 2

    # Eligibility and Schedule
    cov_block = _extract(text, ["Covered Transactions (VM)", "Covered Transactions", "Scope"], stops_except("Covered Transactions"))
    if cov_block:
        # Abstain if generic delegation phrases present
        if re.search(r"as\s+set\s+forth\s+in\s+the\s+Schedule|as\s+agreed|in\s+accordance\s+with\s+Paragraph\s+13", cov_block, re.IGNORECASE):
            pass
        else:
            items = [i.strip() for i in re.split(r"\s*[;•\n]\s+|,\s+", cov_block) if len(i.strip()) > 2]
            if items:
                result["eligibility.covered_transactions"] = items
    else:
        # Inline single-line form: Covered Transactions: <text until period>
        m_cov = re.search(r"Covered\s+Transactions[^:]*:\s*([^\.\n\r]+)", text, re.IGNORECASE)
        if m_cov:
            txt = m_cov.group(1).strip()
            if not re.search(r"as\s+set\s+forth|as\s+agreed|in\s+accordance\s+with\s+Paragraph\s+13", txt, re.IGNORECASE):
                result["eligibility.covered_transactions"] = [txt]

    # Spot FX carveout
    spot_ctx = cov_block or text
    if re.search(r"\bSpot\s+FX\b|\bForeign\s+Exchange\s+Transactions\b|\bFX\s+transactions\b", spot_ctx, re.IGNORECASE):
        if re.search(r"exclud|except|other\s+than|not\s+included|shall\s+not\s+include", spot_ctx, re.IGNORECASE):
            result["eligibility.spot_fx_carveout"] = True
        elif re.search(r"includes?\s+Spot\s+FX", spot_ctx, re.IGNORECASE):
            result["eligibility.spot_fx_carveout"] = False

    # Ratings and issuer constraints
    ratings_block = _extract(text, ["Rated at least", "NRSRO", "ratings"], stops_except("Rated at least"))
    if ratings_block:
        snippet = _extract_anchor_snippet(ratings_block, "Rated") or ratings_block[:300].strip()
        result["eligibility.ratings_condition"] = snippet
    issuer_block = _extract(text, ["Issuer must", "Government of", "Issuer/guarantor", "Government"], stops_except("Issuer must"))
    if issuer_block:
        result["eligibility.issuer_constraints"] = issuer_block[:400].strip()

    # Regime default (heuristic)
    regime_block = _extract(text, ["S&P column", "Moody's First", "Moody's Second", "Fitch", "applicable column", "unless otherwise specified", "review by S&P"], stops_except("S&P column")) or ""
    for key in ["S&P", "Moody's First", "Moody's Second", "Fitch"]:
        if re.search(rf"\b{re.escape(key)}\b", regime_block):
            enum_val = None
            norm = normalize_regime(key)
            if norm == "S&P":
                enum_val = "sp"
            elif norm == "Moody's First Trigger":
                enum_val = "m1"
            elif norm == "Moody's Second Trigger":
                enum_val = "m2"
            elif norm == "Fitch":
                enum_val = "fitch"
            if enum_val:
                result["csa.regime.default"] = enum_val
                break

    # D. Haircuts — FULL EXPANSION
result["haircuts.matrix"] = _parse_haircut_tables(soup)

    # Caps and windows
    m_cash = re.search(r"Cash\s+collateral\s+capped\s+at[^\d%]*([\d]{1,3}(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if m_cash:
        try:
            result["caps_windows.cash_cap_pct_of_U"] = float(m_cash.group(1))
        except ValueError:
            pass
    m_conc = re.search(r"concentration\s+limits?[:\-]?\s*(.+?)(?:\.|\n|\r)", text, re.IGNORECASE)
    if m_conc:
        result["caps_windows.global_cap"] = m_conc.group(1).strip()

    # Regime default detection from table headers or textual hints
    def _collect_regimes_from_tables(sp: BeautifulSoup) -> List[str]:
        regimes: List[str] = []
        for table in sp.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if not cells:
                    continue
                if any(any(k in c for k in ["S&P", "Moody's", "Fitch", "First", "Second"]) for c in cells):
                    for c in cells:
                        reg = normalize_regime(c)
                        if reg != c and reg not in regimes:
                            regimes.append(reg)
                    break
        return regimes

    regimes = _collect_regimes_from_tables(soup)
    def _regime_to_enum(name: str) -> Optional[str]:
        mapping = {
            "S&P": "sp",
            "Moody's First Trigger": "m1",
            "Moody's Second Trigger": "m2",
            "Fitch": "fitch",
        }
        return mapping.get(name)

    if not result.get("csa.regime.default"):
        if len(regimes) == 1:
            enum_val = _regime_to_enum(regimes[0])
            if enum_val:
                result["csa.regime.default"] = enum_val
        else:
            # textual hints
            for key in ["S&P", "Moody's First", "Moody's Second", "Fitch"]:
                if re.search(rf"\b{re.escape(key)}\b[^\n]{{0,40}}(column|default|applicable|unless\s+otherwise\s+specified)", text, re.IGNORECASE):
                    enum_val = _regime_to_enum(normalize_regime(key))
                    if enum_val:
                        result["csa.regime.default"] = enum_val
                        break
            # Special case: "review by S&P" is a strong indicator for S&P default in some CSAs
            if not result.get("csa.regime.default") and re.search(r"review\s+by\s+S&P", text, re.IGNORECASE):
                result["csa.regime.default"] = "sp"

    # Paragraph 13 – Elections and Variables (capture key subsections verbatim)
    _extract_paragraph_13(text, result)
return result


def _extract(text: str, anchors: List[str], stop: List[str]) -> Optional[str]:
idx = find_anchor(text, anchors)
if idx is None:
return None
return extract_paragraph(text, idx, stop)


def _clean_party_name(raw: str) -> str:
    name = raw.strip().strip('"\'\u201C\u201D').strip()
    # Truncate common trailing descriptors (e.g., ", a Delaware corporation")
    m = re.match(r"^(.*?)(?:,?\s+(?:a|an|the)\s+\w.*)$", name, re.IGNORECASE)
    if m:
        name = m.group(1)
    # Remove trailing parentheticals like "(D)"
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    # Collapse internal whitespace
    name = re.sub(r"\s+", " ", name)
    return name.strip(' ,;:.')


def _find_party_name(text: str, label: str) -> Optional[str]:
    # Prefer line-scoped extraction to avoid run-on captures
    lines = text.splitlines()
    label_re = re.compile(rf"[\"\u201C\u201D]?{label}[\"\u201C\u201D]?\s*[:=\-\u2013\u2014]?\s*(.+)$", re.IGNORECASE)
    for line in lines:
        m = label_re.search(line)
        if not m:
            continue
        candidate = m.group(1)
        # Stop at early separators
        candidate = re.split(r"\s{2,}|\s*;|\s*\.\s*", candidate)[0]
        cleaned = _clean_party_name(candidate)
        # Reject obvious non-names or boilerplate
        if re.search(r"\bthis\s+annex\b|paragraph\s+13|schedule\b", cleaned, re.IGNORECASE):
            continue
        if len(cleaned) >= 3 and re.search(r"[A-Z]", cleaned):
            return cleaned
    # Fallback patterns across text
    patterns = [
        rf"[\"\u201C\u201D]?{label}[\"\u201C\u201D]?\s+means\s+([A-Z][A-Za-z0-9&.,'\-()\s]+)",
        rf"([A-Z][A-Za-z0-9&.,'\-()\s]+)\s*\(\s*[\"\u201C\u201D]?{label}[\"\u201C\u201D]?\s*\)",
        rf"([A-Z][A-Za-z0-9&.,'\-()\s]+)\s*[\-\u2013\u2014]\s*{label}\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            cleaned = _clean_party_name(candidate)
            if len(cleaned) >= 3 and re.search(r"[A-Z]", cleaned):
                return cleaned
    return None


def _extract_field_line_value(text: str, label: str) -> Optional[str]:
    lines = text.splitlines()
    label_re = re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE)
    for i, line in enumerate(lines):
        if not label_re.search(line):
            continue
        # Prefer inline value after a separator
        m = re.search(rf"{re.escape(label)}\s*[:=\-\u2013\u2014]?\s*(.+)$", line, re.IGNORECASE)
        candidate = None
        if m and m.group(1).strip():
            candidate = m.group(1).strip()
        else:
            # Next non-empty line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                candidate = lines[j].strip()
        if candidate:
            # Trim to the first sentence-like boundary
            candidate = re.split(r"(?<=\.)\s|\r|\n", candidate)[0]
            return candidate.strip(' ;,')
    return None


def _extract_anchor_snippet(text: str, label: str, max_chars: int = 300) -> Optional[str]:
    # Allow arbitrary punctuation/whitespace between label words
    tokens = re.split(r"\s+", label.strip())
    sep_pattern = r"\W+"  # non-word separators, covers NBSP and punctuation
    label_pattern = sep_pattern.join(map(re.escape, tokens))
    m = re.search(label_pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if not m:
        return None
    start = m.start()
    seg = text[start: start + 1500]
    # Take until the first blank line or next heading-like label
    parts = []
    for line in seg.splitlines():
        if not line.strip():
            break
        if re.search(r"^[A-Z][A-Za-z\s]+:\s*$", line.strip()):
            break
        parts.append(line.strip())
    snip = " ".join(parts)
    snip = re.sub(r"\s+", " ", snip)
    # Remove the label name itself if present (tolerate whitespace)
    snip = re.sub(rf"^{label_pattern}\s*[:=\-\u2013\u2014]?\s*", "", snip, flags=re.IGNORECASE)
    # Drop leading quotes and 'means' if present
    snip = re.sub(r'^["\u201C\u201D\s]*', '', snip)
    snip = re.sub(r'^means\s+', '', snip, flags=re.IGNORECASE)
    if len(snip) > max_chars:
        cut = snip[:max_chars]
        # Prefer to end at a sentence boundary if possible
        last_period = cut.rfind(".")
        if last_period > 50:
            snip = cut[: last_period + 1]
        else:
            snip = cut.strip()
    return snip.strip(' ;,') or None


def _find_parties_between(text: str) -> Optional[tuple]:
    # Heuristic: if the title/preamble contains two 'between', take the parties after the second
    lower = text.lower()
    idxs = [m.start() for m in re.finditer(r"\bbetween\b", lower)]
    if len(idxs) >= 2:
        seg = text[idxs[1] + len("between"): idxs[1] + 1000]
        # Split on ' and '
        m_and = re.search(r"\s+and\s+", seg, re.IGNORECASE)
        if m_and:
            left = seg[: m_and.start()]
            right = seg[m_and.end():]
            # Truncate right at common delimiters
            right = re.split(r"\s+(?:dated|effective|each|,|\.|;|\n|\r|\)|\()", right, 1)[0]
            n1 = _clean_party_name(left)
            n2 = _clean_party_name(right)
            stop = re.compile(r"\b(this\s+annex|this\s+schedule|paragraph\s+\d+|section\s+\d+)\b", re.IGNORECASE)
            if all([
                re.search(r"[A-Z]", n) and not stop.search(n) for n in (n1, n2)
            ]):
                # Apply entity check to avoid generic phrases
                if _is_entity_like_name(n1) and _is_entity_like_name(n2):
                    return n1, n2

    # Capture: between NAME1 and NAME2 ...
    pat_upper = re.compile(
        r"between\s+([A-Z][A-Z0-9&.,'\-()\s]{5,}?)\s+and\s+([A-Z][A-Z0-9&.,'\-()\s]{5,}?)(?:\s|,|\.|;|\)|$)",
        re.IGNORECASE,
    )
    pat_double_between = re.compile(
        r"between\s+.+?between\s+([A-Z][A-Z0-9&.,'\-()\s]{5,}?)\s+and\s+([A-Z][A-Z0-9&.,'\-()\s]{5,}?)(?:\s|,|\.|;|\)|$)",
        re.IGNORECASE | re.DOTALL,
    )
    pat = re.compile(
        r"between\s+([A-Z][A-Za-z0-9&.,'\-()\s]{2,}?)\s+and\s+([A-Z][A-Za-z0-9&.,'\-()\s]{2,}?)(?:\s|,|\.|;|\)|$)",
        re.IGNORECASE,
    )
    # use shared entity heuristic

    best = None
    # Try double-between pattern first (common in EDGAR titles)
    search_text = text[:3000]
    best = None
    for m in list(pat_double_between.finditer(search_text)) + list(pat_upper.finditer(text)) + list(pat.finditer(text)):
        raw1, raw2 = m.group(1), m.group(2)
        # Reject pairs that are just labels
        if re.search(r"\bParty\s+A\b", raw1, re.IGNORECASE) or re.search(r"\bParty\s+B\b", raw1, re.IGNORECASE):
            continue
        if re.search(r"\bParty\s+A\b", raw2, re.IGNORECASE) or re.search(r"\bParty\s+B\b", raw2, re.IGNORECASE):
            continue
        n1, n2 = _clean_party_name(raw1), _clean_party_name(raw2)
        if not (_is_entity_like_name(n1) and _is_entity_like_name(n2)):
            continue
        if re.search(r"\bParty\s+[AB]\b", n1, re.IGNORECASE):
            continue
        if re.search(r"\bParty\s+[AB]\b", n2, re.IGNORECASE):
            continue
        best = (n1, n2)
    return best


def _find_parties_from_signature(text: str) -> Optional[tuple]:
    # Look for signature block after "IN WITNESS WHEREOF" and capture two uppercase legal names
    m = re.search(r"IN\s+WITNESS\s+WHEREOF[\s\S]{0,800}", text, re.IGNORECASE)
    if not m:
        return None
    seg = text[m.end(): m.end() + 1000]
    # Candidate lines: mostly uppercase words, include commas and entity tokens
    candidates = []
    for line in seg.splitlines():
        s = line.strip()
        if len(s) < 4:
            continue
        if len(s) > 120:
            continue
        if re.search(r"By:\s|Name:\s|Title:\s", s, re.IGNORECASE):
            continue
        letters = ''.join(ch for ch in s if ch.isalpha())
        if letters and sum(1 for c in letters if c.isupper()) / max(1, len(letters)) >= 0.5:
            if re.search(r"\b(inc\.?|llc|ltd|plc|bank|association|trust|corporation|company|limited)\b", s, re.IGNORECASE):
                candidates.append(s.rstrip(',;'))
        if len(candidates) >= 2:
            break
    if len(candidates) >= 2:
        return candidates[0], candidates[1]
    return None


def _extract_paragraph_13(text: str, result: Dict[str, Any]) -> None:
    m = re.search(r"Paragraph\s*13\.?\s*(Elections\s+and\s+Variables)?", text, re.IGNORECASE)
    if not m:
        return
    start = m.start()
    # End at next Paragraph heading or end of doc
    m_end = re.search(r"\n\s*Paragraph\s*1[4-9]\b", text[start:], re.IGNORECASE)
    end = start + m_end.start() if m_end else len(text)
    seg = text[start:end]

    # Title
    title_match = re.search(r"Paragraph\s*13\.?\s*(.+)", seg, re.IGNORECASE)
    if title_match:
        result["paragraph_13"]["title"] = title_match.group(1).strip()

    # (a) Security Interest for "Obligations."
    a_match = re.search(r"\(a\)\s*(Security\s+Interest[\s\S]*?)(?=\n\s*\(b\)|$)", seg, re.IGNORECASE)
    if a_match:
        result["paragraph_13"]["a_obligations"] = re.sub(r"\s+", " ", a_match.group(1)).strip()

    # (b)(i) Credit Support Obligations (definitions reference)
    bi_match = re.search(r"\(b\)\s*Credit\s+Support\s+Obligations\.[\s\S]*?\(i\)\s*([\s\S]*?)(?=\n\s*\(ii\)|$)", seg, re.IGNORECASE)
    if bi_match:
        result["paragraph_13"]["b_i_credit_support_obligations"] = re.sub(r"\s+", " ", bi_match.group(1)).strip()

    # (b)(ii) Eligible Collateral (ICAD)
    bii_match = re.search(r"\(ii\)\s*([\s\S]*?)(?=\n\s*\([a-z0-9]\)|\n\s*Paragraph\s*1[4-9]|$)", seg, re.IGNORECASE)
    if bii_match:
        result["paragraph_13"]["b_ii_eligible_collateral_text"] = re.sub(r"\s+", " ", bii_match.group(1)).strip()


def _find_parties_by_labeled_lines(text: str) -> Optional[tuple]:
    # Look for explicit labels like "Party A:" and "Party B:" and capture concise names
    lines = text.splitlines()
    party_a = party_b = None
    for line in lines:
        m = re.search(r"Party\s*A\s*[:=\-]\s*(.+)$", line, re.IGNORECASE)
        if m:
            cand = re.split(r"\s{2,}|;|\.|,\s*(?=[A-Z])", m.group(1).strip())[0]
            cand = _clean_party_name(cand)
            if 2 < len(cand) < 120 and _is_entity_like_name(cand):
                party_a = cand
        m = re.search(r"Party\s*B\s*[:=\-]\s*(.+)$", line, re.IGNORECASE)
        if m:
            cand = re.split(r"\s{2,}|;|\.|,\s*(?=[A-Z])", m.group(1).strip())[0]
            cand = _clean_party_name(cand)
            if 2 < len(cand) < 120 and _is_entity_like_name(cand):
                party_b = cand
        if party_a and party_b:
            break
    if party_a and party_b:
        return party_a, party_b
    return None


def _find_parties_by_parenthetical_labels(text: str) -> Optional[tuple]:
    # Match patterns like NAME ("Party A") or NAME on previous line and ("Party B") on next line
    q = '\\"\u201C\u201D\x93\x94'
    pat_inline_a = re.compile(rf"([A-Z][A-Za-z0-9&.,'\-() ]{{3,}}?)\s*\(\s*[{q}]?Party\s*A[{q}]?\s*\)")
    pat_inline_b = re.compile(rf"([A-Z][A-Za-z0-9&.,'\-() ]{{3,}}?)\s*\(\s*[{q}]?Party\s*B[{q}]?\s*\)")
    a = b = None
    for m in pat_inline_a.finditer(text):
        cand = _clean_party_name(m.group(1))
        if _is_entity_like_name(cand):
            a = cand
            break
    for m in pat_inline_b.finditer(text):
        cand = _clean_party_name(m.group(1))
        if _is_entity_like_name(cand):
            b = cand
            break
    # Line-separated form: find "(Party A/B)" and take previous non-empty line as name
    if not a or not b:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not a and re.search(rf"\(\s*[{q}]?Party\s*A[{q}]?\s*\)", line):
                # previous non-empty
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                if j >= 0:
                    line_prev = lines[j]
                    # If the line contains 'between', keep the substring after it
                    m_between = re.search(r"between\s+(.+)$", line_prev, re.IGNORECASE)
                    if m_between:
                        line_prev = m_between.group(1)
                    cand = _clean_party_name(line_prev)
                    if _is_entity_like_name(cand):
                        a = cand
            if not b and re.search(rf"\(\s*[{q}]?Party\s*B[{q}]?\s*\)", line):
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                if j >= 0:
                    line_prev = lines[j]
                    m_between = re.search(r"between\s+(.+)$", line_prev, re.IGNORECASE)
                    if m_between:
                        line_prev = m_between.group(1)
                    cand = _clean_party_name(line_prev)
                    if _is_entity_like_name(cand):
                        b = cand
            if a and b:
                break
    if a and b:
        return a, b
    return None

def _parse_haircut_tables(soup) -> List[Dict]:
    rows: List[Dict] = []
current_asset = None
header_cells = None
    detected_regimes: List[str] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
if not cells:
continue

            # Regime header
            if any(
                any(
                    k in c
                    for k in [
                        "S&P",
                        "Moody's",
                        "Fitch",
                        "Approved",
                        "Required",
                        "First",
                        "Second",
                    ]
                )
                for c in cells
            ):
header_cells = cells
                # Record normalized regimes from header row
                for c in cells:
                    regime = normalize_regime(c)
                    if regime != c:
                        detected_regimes.append(regime)
continue

            # Asset type
            if len(cells) >= 2 and any(
                k in cells[0]
                for k in [
                    "Cash",
                    "Treasury",
                    "Agency",
                    "Euro-Zone",
                    "Certificates",
                    "Commercial",
                ]
            ):
current_asset = cells[0]
continue

            # Data row
if current_asset and header_cells and len(cells) >= 2:
bucket = cells[0].strip()
for i, pct_cell in enumerate(cells[1:]):
if i >= len(header_cells):
break
val = parse_percentage(pct_cell)
if val is not None:
regime_raw = header_cells[i]
regime = normalize_regime(regime_raw)
                        rows.append(
                            {
"asset_type": current_asset,
"maturity_bucket": bucket,
"regime": regime,
                                "valuation_percentage": val,
                            }
                        )
    # If exactly one regime header type is consistently observed, set default upstream via a marker row
    # We can't directly write to result here; instead, append a sentinel row which parse_html can read if needed
return rows
