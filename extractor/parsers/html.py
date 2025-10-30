from bs4 import BeautifulSoup
from ..clean import clean_text_for_extraction
from typing import Dict, Any, Optional
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


def _extract_date(text: str) -> Optional[str]:
    m = (
        re.search(r"dated\s+as\s+of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
        or re.search(r"as\s+of\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
        or re.search(r"dated\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
    )
    return m.group(1) if m else None


def parse_html(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n")
    text = clean_text_for_extraction(text)

    result = init_result()

    # Governing law (explicit only)
    if re.search(
        r"\bnew\s+york\s+law\b|governed\s+by\s+the\s+laws\s+of\s+the\s+state\s+of\s+new\s+york",
        text,
        re.I,
    ):
        result["csa.meta.governing_law"] = "NY"
    elif re.search(
        r"\benglish\s+law\b|governed\s+by\s+english\s+law|laws\s+of\s+england\s+and\s+wales",
        text,
        re.I,
    ):
        result["csa.meta.governing_law"] = "English"

    # Agreement date (verbatim)
    date_str = _extract_date(text)
    if date_str:
        result["csa.meta.agreement_date"] = date_str

    # Base currency (very narrow, explicit only)
    m_base = re.search(
        r"Base\s+Currency[^A-Za-z0-9]*([A-Z]{3}|U\.S\.\s*Dollars|US\s*Dollars|United\s*States\s*Dollars|Euro|Pounds\s*Sterling|Yen)",
        text,
        re.I,
    )
    if m_base:
        val = m_base.group(1)
        name_to_iso = {
            "U.S. Dollars": "USD",
            "US Dollars": "USD",
            "United States Dollars": "USD",
            "Euro": "EUR",
            "Pounds Sterling": "GBP",
            "Yen": "JPY",
        }
        result["terms.base_currency"] = name_to_iso.get(val, val.upper())

    return result
